"""Shared JSONL viewer for handoff list (detail) and tail commands.

Uses Textual to render Claude stream-json output: compact progress log
(RichLog), input prompt (Markdown), and final result (Markdown).

Modes:
  - static:  list detail page; Escape dismisses back to list
  - follow:  `handoff tail`; Escape / Q exit the app
"""

from __future__ import annotations

import os
import re
import asyncio
from typing import Optional

from markdown_it import MarkdownIt
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Markdown,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)
from textual.containers import VerticalScroll
from textual.binding import Binding
from .config import read_tui_theme
from .jsonl_parser import ParsedEvent, format_event_as_rich, read_events
from .tui import HandoffTuiApp


# ═══════════════════════════════════════════════════════════════════════════════
# JsonlViewerScreen
# ═══════════════════════════════════════════════════════════════════════════════


def _markdown_parser_factory() -> MarkdownIt:
    """Create a Markdown parser with all link/image/autolink tokens disabled.

    Textual 2.1.2's Markdown renderer calls ``Style.from_meta({"@click": action})``
    for every ``link_open`` / ``image`` token.  The resulting meta data can contain
    arbitrary strings (e.g. ``/tmp/a:1`` from ``[x](/tmp/a:1)``) which crash
    Python's marshal with ``ValueError: bad marshal data (unknown type code)``
    downstream in Textual's render pipeline.

    Disabling the relevant MarkdownIt rules ensures such tokens are never emitted
    and the input is rendered as plain text instead.
    """
    md = MarkdownIt("gfm-like", options_update={"linkify": False})
    # Explicit markdown links e.g. [text](...), images e.g. ![alt](...),
    # and autolinks e.g. <https://...>
    md.disable(["link", "image", "autolink"])
    return md


class JsonlViewerScreen(Screen):
    """Shared JSONL viewer screen for handoff list (detail) and tail.

    Layout:
      - RunInfoBar (top bar)
      - TabbedContent (Stream / Prompt / Result)
      - Footer (key bindings)
    """

    BINDINGS = [
        # Tab navigation (shown)
        Binding("tab", "next_tab", "Next", show=True),
        Binding("shift+tab", "prev_tab", "Prev", show=True),
        # Actions (shown, ordered by frequency of use)
        Binding("escape", "back", "← Back", show=True),
        Binding("o", "go_resume", "Open", show=True),
        Binding("c", "copy_session", "Copy", show=True),
        Binding("q", "quit", "Quit", show=True),
        # Numeric tab shortcuts (hidden from Footer, keys still work)
        Binding("1", "show_tab('stream')", "", show=False),
        Binding("2", "show_tab('output')", "", show=False),
        Binding("3", "show_tab('prompt')", "", show=False),
        Binding("4", "show_tab('result')", "", show=False),
        # Scrolling (hidden — Textual built-in keymap covers these)
        Binding("up,k", "scroll_active('up')", "", show=False),
        Binding("down,j", "scroll_active('down')", "", show=False),
        Binding("pageup", "scroll_active('page_up')", "", show=False),
        Binding("pagedown", "scroll_active('page_down')", "", show=False),
        Binding("home", "scroll_active('home')", "", show=False),
        Binding("end", "scroll_active('end')", "", show=False),
    ]

    def __init__(
        self,
        jsonl_path: str,
        prompt_path: str,
        out_path: str,
        result_path: str,
        run_info: dict,
        mode: str = "static",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        # Store parameters before super().__init__
        self._jl_path = jsonl_path
        self._p_path = prompt_path
        self._o_path = out_path
        self._r_path = result_path
        self._r_info = run_info
        self._mode = mode
        self._last_ts = ""
        self._fpos = 0
        self._out_fpos = 0
        self._out_buffer = ""      # partial last line buffer for .out parsing
        self._result_text: Optional[str] = None
        self._last_stream_line = ""
        self._poll_interval = 0.5
        # Auto-follow state
        self._auto_follow = {"stream": True, "output": True}
        self._pending_new_count = {"stream": 0, "output": 0}
        self._keep_polling = True
        super().__init__(name=name, id=id, classes=classes)

    def compose(self) -> ComposeResult:
        ri = self._r_info
        yield Static(
            f"Run: {ri.get('run_id', '?')}  ·  "
            f"{ri.get('date', '?')}  ·  "
            f"cwd: {ri.get('cwd', '?')}",
            id="info_bar",
        )
        with TabbedContent(initial="stream"):
            with TabPane("1 Stream JSONL", id="stream"):
                yield RichLog(id="stream_log", auto_scroll=False, highlight=False, markup=False)
            with TabPane("2 Output .out", id="output"):
                yield RichLog(id="output_log", auto_scroll=False, highlight=False, markup=False)
            with TabPane("3 Prompt", id="prompt"):
                with VerticalScroll(id="prompt_scroll"):
                    yield Static("", id="prompt_header")
                    yield Markdown(
                        "",
                        id="prompt_md",
                        parser_factory=_markdown_parser_factory,
                        open_links=False,
                    )
            with TabPane("4 Result", id="result"):
                with VerticalScroll(id="result_scroll"):
                    yield Static("", id="result_header")
                    yield Markdown(
                        "",
                        id="result_md",
                        parser_factory=_markdown_parser_factory,
                        open_links=False,
                    )
        yield Footer()

    def on_mount(self) -> None:
        # ── Prompt tab ──────────────────────────────────────────────────────
        if os.path.isfile(self._p_path):
            try:
                with open(self._p_path, "r", encoding="utf-8", errors="replace") as f:
                    pt = f.read().strip()
                self.query_one("#prompt_header", Static).update(
                    self._header_line("Prompt", self._p_path)
                )
                if pt:
                    self.query_one("#prompt_md", Markdown).update(pt)
            except (OSError, UnicodeDecodeError):
                pass
        else:
            self.query_one("#prompt_header", Static).update(
                self._header_line("Prompt", self._p_path)
            )

        # ── Result tab ──────────────────────────────────────────────────────
        if os.path.isfile(self._r_path):
            try:
                with open(self._r_path, "r", encoding="utf-8", errors="replace") as f:
                    rt = f.read().strip()
                self.query_one("#result_header", Static).update(
                    self._header_line("Result", self._r_path)
                )
                if rt:
                    self._result_text = rt
                    self.query_one("#result_md", Markdown).update(rt)
            except (OSError, UnicodeDecodeError):
                pass
        else:
            self.query_one("#result_header", Static).update(
                self._header_line("Result", self._r_path)
            )

        # ── Stream tab ──────────────────────────────────────────────────────
        stream_log = self.query_one("#stream_log", RichLog)
        stream_log.write(Text(self._header_line("JSONL", self._jl_path), style="dim"))
        if os.path.isfile(self._jl_path):
            with open(self._jl_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._fpos)
                events, self._last_ts = read_events(f, self._last_ts)
                self._fpos = f.tell()
            self._append_events(events)

        # ── Output tab ──────────────────────────────────────────────────────
        out_log = self.query_one("#output_log", RichLog)
        out_log.write(Text(self._header_line("Output", self._o_path), style="dim"))
        self._append_output_from_file()

        # Scroll to bottom after initial load
        stream_log.scroll_end(animate=False)
        out_log.scroll_end(animate=False)

        # Start poll worker for all modes (live updates for running runs)
        self._poll_jsonl()

    # ── follow worker ────────────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _poll_jsonl(self) -> None:
        while self._keep_polling:
            if os.path.isfile(self._jl_path):
                try:
                    with open(self._jl_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(self._fpos)
                        events, self._last_ts = read_events(f, self._last_ts)
                        self._fpos = f.tell()
                    self._append_events(events)
                except (OSError, UnicodeDecodeError):
                    pass
                except Exception:
                    pass  # screen may have been unmounted

            self._append_output_from_file()

            # Check scroll position to update auto-follow state
            self._sync_auto_follow("stream")
            self._sync_auto_follow("output")

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    def on_unmount(self) -> None:
        """Ensure poll loop exits when screen is removed."""
        self._keep_polling = False

    def _sync_auto_follow(self, tab_id: str) -> None:
        """Update _auto_follow based on current scroll position.

        Both stream and output use RichLog (a ScrollView) directly;
        no VerticalScroll wrapper to query.
        """
        try:
            rl = self.query_one(f"#{tab_id}_log", RichLog)
            self._auto_follow[tab_id] = rl.is_vertical_scroll_end
        except Exception:
            pass

    def _scroll_to_bottom(self, tab_id: str) -> None:
        """Scroll the log widget to its end."""
        try:
            self.query_one(f"#{tab_id}_log", RichLog).scroll_end(animate=False)
        except Exception:
            pass

    def _update_info_bar(self) -> None:
        """Update info bar with auto-follow status."""
        try:
            ri = self._r_info
            parts = [
                f"Run: {ri.get('run_id', '?')}",
                ri.get("date", "?"),
                f"cwd: {ri.get('cwd', '?')}",
            ]
            status_parts = []
            for tab_id, label in (("stream", "stream"), ("output", "output")):
                if self._auto_follow[tab_id]:
                    status_parts.append(f"{label}: follow")
                elif self._pending_new_count[tab_id]:
                    status_parts.append(f"{label}: {self._pending_new_count[tab_id]} new")
                else:
                    status_parts.append(f"{label}: paused")
            parts.append(" | ".join(status_parts))
            self.query_one("#info_bar", Static).update("  ·  ".join(parts))
        except Exception:
            pass

    def _header_line(self, label: str, path: str) -> str:
        return f"{label}: {os.path.abspath(os.path.expanduser(path))}"

    @staticmethod
    def _format_out_line(line: str) -> Text:
        """Format a single .out.txt line with an optional timestamp gutter.

        Lines starting with ``HH:MM:SS`` get the timestamp rendered dim;
        lines without a timestamp prefix (e.g. ``RESULT=...``) get an empty
        8-space gutter for alignment.
        """
        m = re.match(r"^(\d{2}:\d{2}:\d{2})\s+(.*)$", line)
        t = Text()
        if m:
            t.append(f"{m.group(1):8}", style="dim")
            t.append(" │ ", style="dim")
            t.append(m.group(2))
        else:
            t.append(f"{'':8}", style="dim")
            t.append(" │ ", style="dim")
            t.append(line)
        return t

    def _append_output_from_file(self) -> None:
        if not os.path.isfile(self._o_path):
            return
        try:
            size = os.path.getsize(self._o_path)
            if size < self._out_fpos:
                self._out_fpos = 0
                self._out_buffer = ""
            with open(self._o_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self._out_fpos)
                chunk = f.read()
                self._out_fpos = f.tell()
        except OSError:
            return

        # Prepend leftover partial line from the previous read
        if self._out_buffer:
            chunk = self._out_buffer + chunk
            self._out_buffer = ""

        lines = chunk.split("\n")
        # A chunk that doesn't end with \n means the last line is partial
        if not chunk.endswith("\n"):
            self._out_buffer = lines.pop()
        elif lines and lines[-1] == "":
            lines.pop()  # trailing empty element from split

        if not lines:
            return

        self._sync_auto_follow("output")

        try:
            out_log = self.query_one("#output_log", RichLog)
            for line in lines:
                out_log.write(self._format_out_line(line))

            if self._auto_follow["output"]:
                out_log.scroll_end(animate=False)
                self._pending_new_count["output"] = 0
            else:
                self._pending_new_count["output"] += len(lines)
        except Exception:
            return

        self._update_info_bar()

    def _append_events(self, events: list[ParsedEvent]) -> None:
        if not events:
            return

        rich_lines: list[Text] = []
        for event in events:
            # result_text / error_text → Markdown tab
            if event.kind in ("result_text", "error_text"):
                self._result_text = event.text
                try:
                    self.query_one("#result_header", Static).update(
                        self._header_line("Result", self._r_path)
                    )
                    self.query_one("#result_md", Markdown).update(event.text)
                    self.query_one(TabbedContent).active = "result"
                except Exception:
                    pass
                continue

            line = format_event_as_rich(event)
            if line is None:
                continue
            # Skip line if it's identical to the last one (JSONL dedup)
            if line.plain == self._last_stream_line:
                continue
            self._last_stream_line = line.plain
            rich_lines.append(line)

        if not rich_lines:
            return

        self._sync_auto_follow("stream")

        try:
            stream_log = self.query_one("#stream_log", RichLog)
            for rl in rich_lines:
                stream_log.write(rl)

            if self._auto_follow["stream"]:
                stream_log.scroll_end(animate=False)
                self._pending_new_count["stream"] = 0
            else:
                self._pending_new_count["stream"] += len(rich_lines)
        except Exception:
            return

        self._update_info_bar()

    # ── actions ──────────────────────────────────────────────────────────

    def action_back(self) -> None:
        self._keep_polling = False
        if self._mode == "static":
            self.dismiss()
        else:
            self.app.exit()

    def action_go_resume(self) -> None:
        self._keep_polling = False
        rid = self._r_info.get("run_id", "")
        if hasattr(self.app, "_action_result"):
            self.app._action_result = f"resume:{rid}"
        self.app.exit()

    def action_copy_session(self) -> None:
        import subprocess
        uid = self._r_info.get("uuid", "")
        if uid:
            try:
                subprocess.run(["pbcopy"], input=uid, text=True, check=True)
                self.notify(f"Copied: {uid}", severity="information", timeout=3)
            except (subprocess.CalledProcessError, FileNotFoundError):
                self.notify("Copy failed: pbcopy not available", severity="error")

    def action_quit(self) -> None:
        self._keep_polling = False
        self.app.exit()

    def action_show_tab(self, tab_id: str) -> None:
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    def action_next_tab(self) -> None:
        try:
            tabs = self.query_one(TabbedContent)
            ids = [pane.id for pane in tabs.query(TabPane)]
            if not ids:
                return
            cur = tabs.active
            idx = ids.index(cur) if cur in ids else -1
            next_id = ids[(idx + 1) % len(ids)]
            tabs.active = next_id
        except Exception:
            pass

    def action_prev_tab(self) -> None:
        try:
            tabs = self.query_one(TabbedContent)
            ids = [pane.id for pane in tabs.query(TabPane)]
            if not ids:
                return
            cur = tabs.active
            idx = ids.index(cur) if cur in ids else 0
            prev_id = ids[(idx - 1) % len(ids)]
            tabs.active = prev_id
        except Exception:
            pass

    def action_scroll_active(self, direction: str) -> None:
        try:
            active = self.query_one(TabbedContent).active
        except Exception:
            return

        if active in ("stream", "output"):
            # Log tabs use RichLog (a ScrollView) — no VerticalScroll wrapper
            try:
                w = self.query_one(f"#{active}_log", RichLog)
            except Exception:
                return
        else:
            # Markdown tabs use a VerticalScroll wrapper
            try:
                w = self.query_one(f"#{active}_scroll", VerticalScroll)
            except Exception:
                return

        if direction == "up":
            w.scroll_up(animate=False)
        elif direction == "down":
            w.scroll_down(animate=False)
        elif direction == "page_up":
            w.scroll_page_up(animate=False)
        elif direction == "page_down":
            w.scroll_page_down(animate=False)
        elif direction == "home":
            w.scroll_home(animate=False)
        elif direction == "end":
            w.scroll_end(animate=False)

        if active in self._auto_follow:
            self._sync_auto_follow(active)
            if self._auto_follow[active]:
                self._pending_new_count[active] = 0
            self._update_info_bar()


# ═══════════════════════════════════════════════════════════════════════════════
# Tail entry point
# ═══════════════════════════════════════════════════════════════════════════════

class JsonlTailApp(HandoffTuiApp):
    """Standalone Textual app for `handoff tail`."""

    TITLE = "handoff tail"

    def __init__(
        self,
        jsonl_path: str,
        prompt_path: str,
        out_path: str,
        result_path: str,
        run_info: dict,
        theme_name: str | None = None,
    ):
        self._a_jl = jsonl_path
        self._a_pp = prompt_path
        self._a_op = out_path
        self._a_rp = result_path
        self._a_ri = run_info
        super().__init__(theme_name=theme_name)

    def on_mount(self) -> None:
        self.push_screen(JsonlViewerScreen(
            jsonl_path=self._a_jl,
            prompt_path=self._a_pp,
            out_path=self._a_op,
            result_path=self._a_rp,
            run_info=self._a_ri,
            mode="follow",
        ))
        self.apply_initial_theme()


def run_tail(
    jsonl_path: str,
    prompt_path: str,
    result_path: str,
    run_info: dict,
    theme_name: str | None = None,
) -> None:
    """Entry point for `handoff tail`."""
    out_path = run_info.get("out_path", "")
    JsonlTailApp(
        jsonl_path,
        prompt_path,
        out_path,
        result_path,
        run_info,
        theme_name=theme_name,
    ).run(mouse=False)


def make_viewer_screen(
    jsonl_path: str,
    prompt_path: str,
    out_path: str,
    result_path: str,
    run_info: dict,
) -> JsonlViewerScreen:
    """Create a viewer screen for embedding in another Textual app (list detail)."""
    return JsonlViewerScreen(
        jsonl_path=jsonl_path,
        prompt_path=prompt_path,
        out_path=out_path,
        result_path=result_path,
        run_info=run_info,
        mode="static",
    )
