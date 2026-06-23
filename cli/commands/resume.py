"""handoff resume command.

Unifies "reopen a past conversation" into one verb, keyed by seq (or run-id):

  handoff resume <seq>                  — interactive: drop into `claude --resume`
  handoff resume <seq> - <<'EOF' ...    — non-interactive: dispatch a new task to
  handoff resume <seq> --text "..."       that same conversation (claude -p --resume),
                                          running through the normal run pipeline.

The seq → session mapping comes from the runs table: the selected row's
`session_id` is the underlying claude conversation. `--resume` does not fork, so
the original seq stays a stable handle — keep using it to add more turns.
"""

import os
import sys

from ..core import get_db, find_run, row_value
from ..config import Config


def cmd_resume(argv: list[str], config: Config):
    """handoff resume [<run-id|seq>] [--backend <name>] [--slug <slug>] [--pro] [--cwd <dir>]
    [--verbose] [(<input-file|-> | --text <prompt...>)]."""
    # Pre-scan --verbose so it works regardless of position (e.g. after --text).
    verbose = "--verbose" in argv
    filtered = [a for a in argv if a != "--verbose"]

    pro = False
    cwd = ""
    backend_arg = ""
    slug_arg = ""
    selector = ""
    input_src = ""
    text_mode = False
    text_parts = []
    have_selector = False

    i = 0
    while i < len(filtered):
        a = filtered[i]
        if a == "-":
            input_src = "-"
        elif a == "--cwd":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --cwd requires a value", file=sys.stderr)
                sys.exit(2)
            cwd = filtered[i]
        elif a == "--backend":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --backend requires a value", file=sys.stderr)
                sys.exit(2)
            backend_arg = filtered[i]
        elif a.startswith("--backend="):
            backend_arg = a.split("=", 1)[1]
        elif a == "--slug":
            i += 1
            if i >= len(filtered):
                print("handoff resume: --slug requires a value", file=sys.stderr)
                sys.exit(2)
            slug_arg = filtered[i]
        elif a.startswith("--slug="):
            slug_arg = a.split("=", 1)[1]
        elif a == "--text":
            text_mode = True
            if input_src:
                print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
                sys.exit(2)
            if i + 1 >= len(filtered):
                print("handoff resume: --text requires a value", file=sys.stderr)
                sys.exit(2)
            if filtered[i + 1] == "--":
                text_parts.extend(filtered[i + 2:])
            else:
                text_parts.extend(filtered[i + 1:])
            break
        elif a.startswith("--text="):
            text_mode = True
            if input_src:
                print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
                sys.exit(2)
            text_parts.append(a.split("=", 1)[1])
            text_parts.extend(filtered[i + 1:])
            break
        elif a == "--pro":
            pro = True
        elif a in ("-h", "--help"):
            from ..main import usage
            usage()
            sys.exit(0)
        elif a.startswith("-") and a != "-":
            print(f"handoff resume: unknown option {a}", file=sys.stderr)
            sys.exit(2)
        else:
            # First bare positional is the selector (seq/run-id); a second one is
            # an input file (prompt source).
            if not have_selector:
                selector = a
                have_selector = True
            elif text_mode:
                print("handoff resume: --text cannot be combined with an input file", file=sys.stderr)
                sys.exit(2)
            else:
                input_src = a
        i += 1

    # Resolve the target conversation.
    conn = get_db()
    row = find_run(conn, selector or None)

    if not row:
        conn.close()
        print("handoff resume: no run found", file=sys.stderr)
        sys.exit(1)

    session_id = row_value(row, "session_id", "") or row["uuid"]
    row_cwd = row["cwd"]
    saved_backend = row_value(row, "backend", "") or ""

    # Decide prompt source → interactive vs continuation.
    prompt_text = None
    if text_mode:
        prompt_text = " ".join(text_parts)
        if not prompt_text:
            print("handoff resume: --text requires a non-empty value", file=sys.stderr)
            sys.exit(2)
    elif input_src == "-" or (not input_src and not sys.stdin.isatty()):
        prompt_text = sys.stdin.read()
    elif input_src:
        if not os.path.isfile(input_src):
            print(f"handoff resume: input file not found: {input_src}", file=sys.stderr)
            sys.exit(2)
        with open(input_src, encoding="utf-8") as f:
            prompt_text = f.read()

    if not cwd:
        cwd = row_cwd
    if not os.path.isdir(cwd):
        print(f"handoff resume: cwd not found: {cwd}", file=sys.stderr)
        sys.exit(2)

    # A continuation must stay on the conversation's original backend — the
    # session id only means something to the CLI that created it.
    if backend_arg and saved_backend and backend_arg != saved_backend:
        print(
            f"handoff resume: this conversation belongs to backend '{saved_backend}'; "
            f"it cannot be resumed with --backend {backend_arg}. "
            f"Use `handoff run --backend {backend_arg}` to start a new conversation.",
            file=sys.stderr,
        )
        sys.exit(2)
    backend_name = saved_backend or backend_arg or config.default_backend

    if prompt_text is None:
        # Interactive: reopen the conversation in claude (replaces this process).
        conn.close()
        from .open import _open_interactive
        _open_interactive(config, backend_name, session_id, cwd, pro, verbose=verbose)
    else:
        # Non-interactive: dispatch a new turn through the run pipeline.
        conn.close()
        from .run import _execute
        _execute(
            cwd,
            prompt_text,
            backend_name,
            pro,
            config,
            resume_session_id=session_id,
            slug=slug_arg or "resume",
            verbose=verbose,
        )
