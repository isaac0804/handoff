"""handoff list command."""

import sys

from ..core import find_run, format_run_row, get_db
from ..config import Config


def cmd_list(argv: list[str], config: Config):
    """handoff list [<run-id|seq>] [--uuid] [--cwd] [--follow]"""
    show_uuid = False
    full_cwd = False
    follow = False
    selector = ""

    for a in argv:
        if a == "--uuid":
            show_uuid = True
        elif a == "--cwd":
            full_cwd = True
        elif a == "--follow":
            follow = True
        elif a in ("-h", "--help"):
            from ..main import usage
            usage()
            sys.exit(0)
        elif a.startswith("-"):
            print(f"handoff list: unknown argument {a}", file=sys.stderr)
            sys.exit(2)
        else:
            if selector:
                print(f"handoff list: unexpected extra argument {a}", file=sys.stderr)
                sys.exit(2)
            selector = a

    conn = get_db()

    def _recent_rows():
        return conn.execute(
            "SELECT seq, run_id, uuid, cwd, prompt, created_at, jsonl_path, status, backend, runtime_info "
            "FROM runs ORDER BY created_at DESC LIMIT 50"
        ).fetchall()

    def _ensure_row(rows, pinned_run_id: str | None):
        if not pinned_run_id:
            return rows
        if any(r["run_id"] == pinned_run_id for r in rows):
            return rows
        pinned = find_run(conn, pinned_run_id)
        if not pinned:
            return rows
        return [pinned, *rows]

    rows = _recent_rows()
    selected_row = find_run(conn, selector or None) if selector else None
    if selector and not selected_row:
        conn.close()
        print("handoff list: no run found", file=sys.stderr)
        sys.exit(1)

    initial_run_id = selected_row["run_id"] if selected_row else ""
    rows = _ensure_row(rows, initial_run_id or None)

    if not rows:
        conn.close()
        print("(no runs)")
        return

    if sys.stdin.isatty() and sys.stdout.isatty():
        # Launch the TUI directly (textual is a package dependency now).
        from ..tui import RunListApp
        from ..config import read_tui_theme

        def _refresh_rows():
            """Re-query the DB for the latest 50 runs. Called by the TUI timer."""
            return _ensure_row(_recent_rows(), initial_run_id or None)

        if follow and not initial_run_id:
            initial_run_id = rows[0]["run_id"]

        app = RunListApp(
            rows,
            full_cwd,
            refresh_fn=_refresh_rows,
            theme_name=read_tui_theme(),
            initial_run_id=initial_run_id or None,
            open_detail_on_mount=follow,
        )
        app.run(mouse=False)
        conn.close()

        if app.action_result and app.action_result.startswith("resume:"):
            run_id = app.action_result[len("resume:"):]
            from .open import cmd_open

            cmd_open([run_id], config)
        return

    conn.close()

    if follow:
        print("handoff list: --follow requires a TTY", file=sys.stderr)
        sys.exit(2)

    rows_to_print = [selected_row] if selected_row else rows

    header = ["RUN", "DATE", "STATUS", "TOKENS", "CWD", "PROMPT"]
    if show_uuid:
        header.append("UUID")

    lines = ["  ".join(header)]
    for r in rows_to_print:
        fmt = format_run_row(r, full_cwd)
        cols = [
            fmt["id"].ljust(34),
            fmt["date"].ljust(11),
            fmt["status"].ljust(11),
            fmt["tokens"].rjust(24),
            fmt["cwd"].ljust(24),
            fmt["prompt"],
        ]
        if show_uuid:
            cols.append(fmt["uuid"])
        lines.append("  ".join(cols))

    print("\n".join(lines))
