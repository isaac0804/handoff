"""Textual-based TUI for interactive run listing and detail viewing in handoff."""

from __future__ import annotations

import os
import sqlite3
from typing import Optional, Callable

from textual.app import App, ComposeResult, InvalidThemeError
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Static
from textual.binding import Binding
from textual.coordinate import Coordinate

from .config import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, read_tui_theme, write_tui_theme
from .core import format_run_row, get_db, prompt_prefix, row_value, task_paths
from .runtime_info import (
    dump_runtime_info,
    format_usage_detail_value,
    format_usage_value,
    kill_process_group,
    parse_runtime_info,
    runtime_pid,
    scan_jsonl_usage,
)

# Seconds between DB polls for auto-refresh.
POLL_INTERVAL = 5.0


class KillRunError(Exception):
    """Raised when a running task cannot be killed from the TUI."""


class KillConfirmScreen(ModalScreen[bool]):
    """Confirm killing a running task."""

    CSS = """
    KillConfirmScreen {
        align: center middle;
    }

    #kill_dialog {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }

    #kill_title {
        text-style: bold;
        margin-bottom: 1;
    }

    #kill_message {
        margin-bottom: 1;
    }

    #kill_buttons {
        height: auto;
        align-horizontal: right;
    }

    #kill_buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Kill", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(self, run_id: str):
        self._run_id = run_id
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Kill running task?", id="kill_title"),
            Static(
                f"Send SIGTERM to the process group for {self._run_id}.",
                id="kill_message",
            ),
            Horizontal(
                Button("Cancel", id="cancel", variant="default"),
                Button("Kill", id="kill", variant="error"),
                id="kill_buttons",
            ),
            id="kill_dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#kill", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "kill")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


def _runtime_info_column_exists(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "runtime_info" for row in conn.execute("PRAGMA table_info(runs)"))


def _load_runtime_info(run_id: str) -> dict:
    """Load runtime_info JSON for a run without requiring list rows to include it."""
    conn = get_db()
    try:
        if not _runtime_info_column_exists(conn):
            raise KillRunError("runtime_info column is not available yet")

        row = conn.execute(
            "SELECT runtime_info FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KillRunError("run no longer exists")

        info = parse_runtime_info(row["runtime_info"] if row else None)
        if not isinstance(info, dict):
            raise KillRunError("runtime_info is not a JSON object")
        return info
    finally:
        conn.close()


def _runtime_pid(info: dict) -> int:
    pid = runtime_pid(dump_runtime_info(info))
    if pid <= 0:
        raise KillRunError("running task has no runtime pid")
    return pid


def _kill_process_group(pid: int) -> None:
    try:
        kill_process_group(pid)
    except ProcessLookupError as exc:
        raise KillRunError(f"process {pid} is not running") from exc
    except PermissionError as exc:
        raise KillRunError(f"permission denied killing process group for {pid}") from exc
    except OSError as exc:
        raise KillRunError(f"failed to kill process group for {pid}: {exc}") from exc


def _mark_run_interrupted(run_id: str, info: dict) -> None:
    info = dict(info)
    info.pop("pid", None)
    conn = get_db()
    try:
        if not _runtime_info_column_exists(conn):
            raise KillRunError("runtime_info column is not available yet")

        cursor = conn.execute(
            "UPDATE runs SET status = ?, runtime_info = ? "
            "WHERE run_id = ? AND status = ?",
            ("interrupted", dump_runtime_info(info), run_id, "running"),
        )
        conn.commit()
        if cursor.rowcount == 0:
            raise KillRunError("run is no longer running")
    except sqlite3.Error as exc:
        raise KillRunError(f"failed to update run status: {exc}") from exc
    finally:
        conn.close()


class HandoffTuiApp(App):
    """Shared Textual app behavior for handoff TUI screens."""

    BINDINGS = [
        Binding("d", "cycle_theme", "Theme", show=True),
    ]

    def __init__(self, *args, theme_name: str | None = None, **kwargs):
        self._initial_theme_name = theme_name or read_tui_theme()
        super().__init__(*args, **kwargs)

    def apply_initial_theme(self) -> None:
        self._set_theme(self._initial_theme_name, quiet=False)

    def _set_theme(self, theme_name: str, *, quiet: bool) -> str:
        try:
            self.theme = theme_name
            return theme_name
        except InvalidThemeError:
            self.theme = DEFAULT_DARK_THEME
            if not quiet:
                self.notify(
                    f"Unknown theme: {theme_name}. Using {DEFAULT_DARK_THEME}.",
                    severity="warning",
                    timeout=3,
                )
            return DEFAULT_DARK_THEME

    def action_cycle_theme(self) -> None:
        next_theme = (
            DEFAULT_LIGHT_THEME if self.current_theme.dark else DEFAULT_DARK_THEME
        )
        applied_theme = self._set_theme(next_theme, quiet=False)
        write_tui_theme(applied_theme)
        self.notify(f"Theme saved: {applied_theme}", severity="information", timeout=2)


class RunListScreen(Screen):
    """Main screen showing the run list in a DataTable.

    Key bindings:
      Enter / →   — open detail view for the selected run
      O           — resume the selected run's session
      C           — copy session UUID to clipboard
      X           — kill the selected running task
      Q           — quit
    """

    BINDINGS = [
        Binding("right,space", "select_run", "Detail", show=True),
        Binding("o", "go_resume", "Open", show=True),
        Binding("c", "copy_session", "Copy", show=True),
        Binding("x", "kill_run", "Kill", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        rows: list,
        full_cwd: bool = False,
        refresh_fn: Callable[[], list] | None = None,
        initial_run_id: str | None = None,
        open_detail_on_mount: bool = False,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        self._rows = rows          # sqlite3.Row objects
        self._full_cwd = full_cwd
        self._result: Optional[str] = None  # "resume:<run_id>" or None
        self._refresh_fn = refresh_fn
        self._initial_run_id = initial_run_id
        self._open_detail_on_mount = open_detail_on_mount
        self._fingerprint: str = ""               # change-detection fingerprint
        self._dirty: bool = False                 # data changed while detail view was active
        self._pending_cursor_run_id: str | None = None  # cursor-restore target
        super().__init__(name=name, id=id, classes=classes)

    @property
    def action_result(self) -> Optional[str]:
        return self._result

    def compose(self) -> ComposeResult:
        count = len(self._rows)
        run_label = "run" if count == 1 else "runs"
        yield Static(f" handoff runs  ·  {count} recent {run_label}", id="title_bar")
        yield DataTable(id="run_table", cursor_type="row")
        yield Static("", id="run_footer")

    def on_mount(self) -> None:
        table = self.query_one("#run_table", DataTable)
        self._add_columns(table)

        if not self._rows:
            table.add_row("(no runs)", "", "", "", "", "")
            self._update_footer()
            return

        for row in self._rows:
            self._add_table_row(table, row)

        table.focus()

        if self._initial_run_id:
            self._pending_cursor_run_id = self._initial_run_id
            self._restore_cursor()

        # Start periodic DB polling for auto-refresh
        if self._refresh_fn is not None:
            self._fingerprint = self._compute_fingerprint(self._rows)
            self.set_interval(POLL_INTERVAL, self._poll_refresh)

        if self._open_detail_on_mount:
            self._open_detail()
        self._update_footer()

    def _selected_row(self):
        """Return the sqlite3.Row for the currently selected table row."""
        table = self.query_one("#run_table", DataTable)
        if table.row_count == 0:
            return None
        rc = table.cursor_row
        if rc >= len(self._rows):
            return None
        return self._rows[rc]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Handle Enter key on a DataTable row."""
        event.stop()
        self._open_detail()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh footer token details as the cursor moves."""
        event.stop()
        self._update_footer()

    def action_select_run(self) -> None:
        """Open detail view for the selected run."""
        self._open_detail()

    def _open_detail(self) -> None:
        """Shared detail-opening logic."""
        row = self._selected_row()
        if row is None:
            return

        jsonl_path = row["jsonl_path"]
        run_id = row["run_id"]
        prompt_path, out_path, result_path = task_paths(run_id)

        run_info = {
            "run_id": run_id,
            "date": row["created_at"],
            "cwd": row["cwd"],
            "uuid": row["uuid"],
            "out_path": out_path,
            "backend": row["backend"],
        }

        from .jsonl_viewer import make_viewer_screen
        viewer = make_viewer_screen(jsonl_path, prompt_path, out_path, result_path, run_info)
        self.app.push_screen(viewer)

    def action_go_resume(self) -> None:
        """Resume the selected session."""
        row = self._selected_row()
        if row is None:
            return
        self._result = f"resume:{row['run_id']}"
        # Write result to app so cmd_list can read it after run() returns
        if hasattr(self.app, '_action_result'):
            self.app._action_result = self._result
        self.app.exit()

    def action_copy_session(self) -> None:
        """Copy session UUID to clipboard."""
        import subprocess
        row = self._selected_row()
        if row is None:
            return
        uid = row["uuid"]
        if uid:
            try:
                subprocess.run(["pbcopy"], input=uid, text=True, check=True)
                self.notify(f"Copied: {uid}", severity="information", timeout=3)
            except (subprocess.CalledProcessError, FileNotFoundError):
                self.notify("Copy failed: pbcopy not available", severity="error")

    def action_kill_run(self) -> None:
        """Confirm and kill the selected running task."""
        row = self._selected_row()
        if row is None:
            return

        run_id = row["run_id"]
        if row["status"] != "running":
            self.notify("Only running tasks can be killed", severity="warning", timeout=3)
            return

        self.app.push_screen(KillConfirmScreen(run_id), self._kill_run_after_confirm)

    def _kill_run_after_confirm(self, confirmed: bool) -> None:
        if not confirmed:
            return

        row = self._selected_row()
        if row is None:
            return
        run_id = row["run_id"]

        try:
            info = _load_runtime_info(run_id)
            pid = _runtime_pid(info)
            _kill_process_group(pid)
            _mark_run_interrupted(run_id, info)
        except KillRunError as exc:
            self.notify(f"Kill failed: {exc}", severity="error", timeout=5)
            return

        self.notify(f"Killed: {run_id}", severity="information", timeout=3)
        self._refresh_now()

    def action_quit(self) -> None:
        self.app.exit()

    # ── auto-refresh ───────────────────────────────────────────────────────

    @staticmethod
    def _compute_fingerprint(rows: list) -> str:
        """Lightweight change-detection fingerprint: run_id:status per row."""
        parts = []
        for row in rows:
            jsonl_fp = ""
            if row["status"] == "running":
                try:
                    st = os.stat(row["jsonl_path"])
                    jsonl_fp = f":{st.st_size}:{st.st_mtime_ns}"
                except OSError:
                    jsonl_fp = ""
            parts.append(
                f"{row['run_id']}:{row['status']}:{row_value(row, 'runtime_info', '')}{jsonl_fp}"
            )
        return "|".join(parts)

    def _terminal_width(self) -> int:
        try:
            width = int(self.app.size.width)
            if width > 0:
                return width
        except Exception:
            pass
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 120

    def _prompt_width(self) -> int:
        cwd_width = 28 if self._full_cwd else 18
        fixed = 34 + 11 + 11 + cwd_width + 18
        return max(40, self._terminal_width() - fixed)

    def _add_columns(self, table: DataTable) -> None:
        cwd_width = 28 if self._full_cwd else 18
        table.add_column("RUN", width=34, key="run")
        table.add_column("DATE", width=11, key="date")
        table.add_column("STATUS", width=11, key="status")
        table.add_column("CWD", width=cwd_width, key="cwd")
        table.add_column("PROMPT", width=self._prompt_width(), key="prompt")

    def _add_table_row(self, table: DataTable, row) -> None:
        fmt = format_run_row(row, self._full_cwd)
        table.add_row(
            fmt["id"],
            fmt["date"],
            fmt.get("status", ""),
            fmt["cwd"],
            self._prompt_for_row(row),
            key=fmt["id"],
        )

    @staticmethod
    def _row_is_pro(row) -> bool:
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        return bool(info.get("pro"))

    def _prompt_for_row(self, row) -> str:
        width = self._prompt_width()
        prefix = "[Pro] " if self._row_is_pro(row) else ""
        body_width = max(1, width - len(prefix))
        return prefix + prompt_prefix(row["prompt"], body_width)

    def _usage_for_row(self, row) -> dict:
        if row["status"] == "running":
            usage = scan_jsonl_usage(row["jsonl_path"], row_value(row, "backend", "") or "")
            if format_usage_value(usage) != "-":
                return usage
        info = parse_runtime_info(row_value(row, "runtime_info", ""))
        usage = info.get("usage")
        return usage if isinstance(usage, dict) else {}

    def _token_detail_for_row(self, row) -> str:
        return format_usage_detail_value(self._usage_for_row(row))

    def _update_footer(self) -> None:
        try:
            footer = self.query_one("#run_footer", Static)
        except Exception:
            return
        row = self._selected_row()
        detail = self._token_detail_for_row(row) if row is not None else ""
        left = " →/Space Detail   O Open   C Copy   X Kill   Q Quit"
        right = f"{detail}   ^P Palette" if detail else "^P Palette"
        width = self._terminal_width()
        if len(left) + len(right) + 1 > width:
            max_left = max(0, width - len(right) - 4)
            left = left[:max_left].rstrip()
        gap = max(1, width - len(left) - len(right))
        footer.update(left + (" " * gap) + right)

    def _save_cursor_run_id(self) -> None:
        """Remember the currently selected run_id before a table rebuild."""
        if not self._rows:
            self._pending_cursor_run_id = None
            return
        try:
            table = self.query_one("#run_table", DataTable)
            if table.row_count > 0:
                rc = table.cursor_row
                if 0 <= rc < len(self._rows):
                    self._pending_cursor_run_id = self._rows[rc]["run_id"]
                    return
        except Exception:
            pass
        self._pending_cursor_run_id = None

    def _restore_cursor(self) -> None:
        """Move DataTable cursor to the previously selected run_id."""
        if self._pending_cursor_run_id is None:
            return
        target_id = self._pending_cursor_run_id
        self._pending_cursor_run_id = None

        for i, row in enumerate(self._rows):
            if row["run_id"] == target_id:
                try:
                    table = self.query_one("#run_table", DataTable)
                    if i < table.row_count:
                        table.cursor_coordinate = Coordinate(i, 0)
                except Exception:
                    pass
                return

    def _rebuild_table(self) -> None:
        """Clear and repopulate the DataTable from self._rows in place."""
        table = self.query_one("#run_table", DataTable)
        is_active = self.app.screen is self
        had_focus = table.has_focus if is_active else False

        table.clear()

        if not self._rows:
            table.add_row("(no runs)", "", "", "", "")
            self.query_one("#title_bar", Static).update(" handoff runs  ·  0 runs")
            self._update_footer()
            return

        for row in self._rows:
            self._add_table_row(table, row)

        # Refresh title-bar count
        count = len(self._rows)
        run_label = "run" if count == 1 else "runs"
        self.query_one("#title_bar", Static).update(
            f" handoff runs  ·  {count} recent {run_label}"
        )

        self._restore_cursor()

        if had_focus:
            table.focus()
        self._update_footer()

    def _poll_refresh(self) -> None:
        """Periodic timer callback: check for new/changed runs from the DB."""
        if self._refresh_fn is None:
            return

        try:
            fresh_rows = self._refresh_fn()
            if fresh_rows is None:
                return

            new_fp = self._compute_fingerprint(fresh_rows)
            if new_fp == self._fingerprint:
                return  # nothing changed
        except Exception:
            return  # transient DB error — skip this tick

        # Data changed — save cursor, update rows/fingerprint, rebuild
        self._save_cursor_run_id()
        self._fingerprint = new_fp
        self._rows = fresh_rows

        if self.app.screen is self:
            # List screen is active → rebuild immediately.
            self._rebuild_table()
        else:
            # Detail view (or another screen) is on top → defer rebuild so the
            # user isn't kicked back to the list.  Data is already updated;
            # rebuild happens on the next poll tick after the screen resumes.
            self._dirty = True

    def _on_screen_resume(self) -> None:
        """Called by Textual when this screen becomes active again after a pop."""
        super()._on_screen_resume()
        if self._dirty:
            self._dirty = False
            self._rebuild_table()

    def _refresh_now(self) -> None:
        """Refresh immediately after an action mutates the selected run."""
        if self._refresh_fn is None:
            return
        try:
            fresh_rows = self._refresh_fn()
        except Exception:
            return
        if fresh_rows is None:
            return

        self._save_cursor_run_id()
        self._rows = fresh_rows
        self._fingerprint = self._compute_fingerprint(fresh_rows)
        if self.app.screen is self:
            self._rebuild_table()
        else:
            self._dirty = True


class RunListApp(HandoffTuiApp):
    """Textual app wrapping the run list screen.

    Usage:
        app = RunListApp(rows, full_cwd)
        app.run()
        if app.action_result:
            # app.action_result == "resume:<run_id>"
            ...
    """

    TITLE = "handoff list"
    CSS = """
    #title_bar {
        dock: top;
        height: 1;
        background: $accent;
        color: $text;
        text-style: bold;
        padding: 0 1;
    }
    #run_table {
        height: 1fr;
    }
    #run_footer {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        rows: list,
        full_cwd: bool = False,
        refresh_fn: Callable[[], list] | None = None,
        theme_name: str | None = None,
        initial_run_id: str | None = None,
        open_detail_on_mount: bool = False,
    ):
        self._rows = rows
        self._full_cwd = full_cwd
        self._refresh_fn = refresh_fn
        self._initial_run_id = initial_run_id
        self._open_detail_on_mount = open_detail_on_mount
        self._action_result: Optional[str] = None
        super().__init__(theme_name=theme_name)

    @property
    def action_result(self) -> Optional[str]:
        return self._action_result

    def on_mount(self) -> None:
        screen = RunListScreen(
            self._rows,
            self._full_cwd,
            refresh_fn=self._refresh_fn,
            initial_run_id=self._initial_run_id,
            open_detail_on_mount=self._open_detail_on_mount,
        )
        self.push_screen(screen)
        self.apply_initial_theme()

    def on_screen_dismiss(self, event: Screen.Dismissed) -> None:
        """Capture action result when a screen is dismissed."""
        if event.result and isinstance(event.result, str):
            self._action_result = event.result
