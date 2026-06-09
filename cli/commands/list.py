"""ds-cli list command."""

import os
import subprocess
import sys

from ..core import get_db, format_run_row
from ..config import Config


def cmd_list(argv: list[str], config: Config):
    """ds-cli list [--uuid] [--cwd]"""
    show_uuid = False
    full_cwd = False

    for a in argv:
        if a == "--uuid":
            show_uuid = True
        elif a == "--cwd":
            full_cwd = True
        elif a in ("-h", "--help"):
            from ..main import usage
            usage()
            sys.exit(0)
        else:
            print(f"ds-cli list: unknown argument {a}", file=sys.stderr)
            sys.exit(2)

    conn = get_db()
    rows = conn.execute(
        "SELECT seq, run_id, uuid, cwd, prompt, created_at, jsonl_path, status, backend "
        "FROM runs ORDER BY created_at DESC LIMIT 50"
    ).fetchall()

    if not rows:
        conn.close()
        print("(no runs)")
        return

    if sys.stdin.isatty() and sys.stdout.isatty():
        conn.close()

        # Find the TUI launcher script relative to this file.
        _repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        tui_script = os.path.join(_repo_root, "ds-cli-tui")

        tui_args = ["uv", "run", "--script", tui_script]
        if full_cwd:
            tui_args.append("--cwd")

        # Launch as subprocess, inheriting the tty.  The child owns the
        # terminal for the lifetime of the TUI.  If the user triggers a
        # resume, the child handles it directly (os.execvp → claude), so
        # there is nothing to pass back to this parent process.
        subprocess.run(tui_args, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        return

    conn.close()

    header = ["RUN", "DATE", "PROMPT", "CWD"]
    if show_uuid:
        header.append("UUID")

    lines = ["  ".join(header)]
    for r in rows:
        fmt = format_run_row(r, full_cwd)
        cols = [
            fmt["id"].ljust(13),
            fmt["date"].ljust(11),
            fmt["prompt"].ljust(30),
            fmt["cwd"],
        ]
        if show_uuid:
            cols.append(fmt["uuid"])
        lines.append("  ".join(cols))

    print("\n".join(lines))
