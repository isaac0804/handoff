"""Curses-based TUI for interactive run listing in ds-cli."""

import curses
import time
import textwrap
import subprocess

from .core import format_run_row
from .stream import read_tail_lines


def safe_addnstr(stdscr, y: int, x: int, text: str, n: int, attr=0):
    height, width = stdscr.getmaxyx()
    max_n = width - x - 1
    if y < 0 or y >= height or x < 0 or x >= width or n <= 0 or max_n <= 0:
        return
    try:
        stdscr.addnstr(y, x, text, min(n, max_n), attr)
    except curses.error:
        pass


def draw_toolbar(stdscr, y: int, width: int, parts, status: str, base_attr, success_attr, error_attr):
    safe_addnstr(stdscr, y, 0, " " * max(0, width - 1), width - 1, base_attr)
    x = 0
    for text, attr in parts:
        safe_addnstr(stdscr, y, x, text, width - x - 1, attr)
        x += len(text)
        if x >= width - 1:
            return
    if status:
        status_attr = error_attr if "failed" in status or "error" in status else success_attr
        text = f"  |  {status}"
        safe_addnstr(stdscr, y, x, text, width - x - 1, status_attr)


def list_tui(stdscr, rows, full_cwd: bool):
    """Interactive TUI for run listing. Returns ('go', run_id) or None."""
    curses.curs_set(0)
    stdscr.timeout(500)
    selected = 0
    offset = 0
    status = ""
    mode = "list"
    last_detail_refresh = 0.0
    detail_lines = []
    detail_top = 0       # index of first visible detail line
    detail_follow = True  # pin to bottom as live tail grows

    # ── color setup ──────────────────────────────────────────────────────
    use_color = curses.has_colors()
    if use_color:
        try:
            curses.start_color()
            curses.use_default_colors()
            bg = -1  # default (transparent) background
            curses.init_pair(1, curses.COLOR_CYAN, bg)        # header
            curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selected
            curses.init_pair(3, curses.COLOR_GREEN, bg)        # success
            curses.init_pair(4, curses.COLOR_RED, bg)          # error
        except curses.error:
            use_color = False

    A_HEADER = curses.color_pair(1) | curses.A_BOLD if use_color else curses.A_BOLD
    A_SELECTED = curses.color_pair(2) if use_color else curses.A_REVERSE
    A_TOOLBAR = curses.A_REVERSE
    A_KEY = curses.color_pair(1) | curses.A_REVERSE | curses.A_BOLD if use_color else curses.A_REVERSE | curses.A_BOLD
    A_SUCCESS = curses.color_pair(3) | curses.A_REVERSE if use_color else curses.A_REVERSE
    A_ERROR = curses.color_pair(4) | curses.A_REVERSE if use_color else curses.A_REVERSE
    A_NORMAL = 0

    toolbar_parts = [
        ("[↑↓/jk]", A_KEY), (" Move  ", A_TOOLBAR),
        ("[→/Enter]", A_KEY), (" View  ", A_TOOLBAR),
        ("[G]", A_KEY), (" Resume  ", A_TOOLBAR),
        ("[C]", A_KEY), (" Copy Session  ", A_TOOLBAR),
        ("[q]", A_KEY), (" Quit", A_TOOLBAR),
    ]

    def update_detail():
        nonlocal detail_lines, last_detail_refresh
        now = time.time()
        if now - last_detail_refresh > 1.0:
            detail_lines = read_tail_lines(rows[selected]["jsonl_path"], max_lines=80)
            last_detail_refresh = now

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()

        if mode == "list":
            body_h = max(1, height - 3)
            if selected < offset:
                offset = selected
            if selected >= offset + body_h:
                offset = selected - body_h + 1

            # Header row
            header = f"{'RUN':13}  {'DATE':11}  {'PROMPT':30}  CWD"
            safe_addnstr(stdscr, 0, 0, header, width - 1, A_HEADER)

            # Rows
            for screen_y, row in enumerate(rows[offset:offset + body_h], start=1):
                idx = offset + screen_y - 1
                fmt = format_run_row(row, full_cwd)
                line = (
                    f"{fmt['id']:13}  {fmt['date']:11}  "
                    f"{fmt['prompt'][:30]:30}  {fmt['cwd']}"
                )
                attr = A_SELECTED if idx == selected else A_NORMAL
                safe_addnstr(stdscr, screen_y, 0, line.ljust(width - 1), width - 1, attr)

            draw_toolbar(stdscr, height - 1, width, toolbar_parts, status, A_TOOLBAR, A_SUCCESS, A_ERROR)

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = min(len(rows) - 1, selected + 1)
            elif key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT):
                mode = "detail"
                last_detail_refresh = 0.0
                detail_lines = []
                detail_top = 0
                detail_follow = True
            elif key in (ord("g"), ord("G")):
                return ("go", rows[selected]["run_id"])
            elif key in (ord("c"), ord("C")):
                session_id = rows[selected]["uuid"]
                try:
                    subprocess.run(["pbcopy"], input=session_id, text=True, check=True)
                    status = f"copied session {session_id}"
                except (subprocess.CalledProcessError, FileNotFoundError):
                    status = "copy failed: pbcopy not available"
            elif key in (ord("q"), ord("Q"), 27):
                return None
        else:
            # ── detail view ──────────────────────────────────────────
            update_detail()

            row = rows[selected]
            fmt = format_run_row(row, True)

            # Info header: seq id, uuid, cwd, date — then the full input prompt.
            wrap_w = max(1, width - 1)
            info_lines = [
                f"{fmt['id']}  ({row['seq']})",
                f"uuid : {row['uuid']}",
                f"cwd  : {fmt['cwd']}",
                f"date : {row['created_at']}",
                "",
            ]
            prompt_text = (row["prompt"] or "").strip()
            for para in prompt_text.split("\n"):
                wrapped = textwrap.wrap(para, wrap_w) if para else [""]
                info_lines.extend(wrapped)
            info_lines.append("")
            info_lines.append("─" * wrap_w)

            body_h = max(1, height - 3)
            content = info_lines + detail_lines
            max_top = max(0, len(content) - body_h)
            if detail_follow:
                detail_top = max_top
            else:
                detail_top = min(detail_top, max_top)
            start = detail_top
            visible = content[start:start + body_h]
            for y, line in enumerate(visible, start=1):
                content_idx = start + y - 1
                attr = A_HEADER if content_idx < 4 else A_NORMAL
                safe_addnstr(stdscr, y, 0, line, width - 1, attr)

            scroll_hint = "" if detail_follow else f"  {start + 1}-{start + len(visible)}/{len(content)}"
            draw_toolbar(
                stdscr,
                height - 1,
                width,
                [("[←/Esc]", A_KEY), (" Back  ", A_TOOLBAR),
                 ("[↑↓/jk]", A_KEY), (" Scroll  ", A_TOOLBAR),
                 ("[g/G]", A_KEY), (" Top/End", A_TOOLBAR)],
                scroll_hint,
                A_TOOLBAR,
                A_SUCCESS,
                A_ERROR,
            )

            key = stdscr.getch()
            if key in (27, curses.KEY_LEFT):
                mode = "list"
            elif key in (curses.KEY_UP, ord("k")):
                detail_follow = False
                detail_top = max(0, detail_top - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                detail_top = min(max_top, detail_top + 1)
                detail_follow = detail_top >= max_top
            elif key in (curses.KEY_PPAGE,):
                detail_follow = False
                detail_top = max(0, detail_top - body_h)
            elif key in (curses.KEY_NPAGE,):
                detail_top = min(max_top, detail_top + body_h)
                detail_follow = detail_top >= max_top
            elif key in (ord("g"), curses.KEY_HOME):
                detail_follow = False
                detail_top = 0
            elif key in (ord("G"), curses.KEY_END):
                detail_follow = True
            elif key in (ord("q"), ord("Q")):
                return None
