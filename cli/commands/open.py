"""handoff open command."""

from __future__ import annotations

import os
import sys

from ..backend import (
    backend_type,
    build_resume_args,
    format_shell_command,
    resolved_backend_env,
    resolve_backend_model,
)
from ..core import find_run, get_db, row_value
from ..runtime_info import parse_runtime_info
from ..config import Config


_OPEN_DROP_ENV_KEYS = {"ANTHROPIC_CLAUDECODE_PERMISSION_ACCEPT_ALL"}


def cmd_open(argv: list[str], config: Config):
    """handoff open [<run-id|seq>] [--pro] [--cwd <dir>] [--backend <name>] [--verbose]."""
    verbose = "--verbose" in argv
    filtered = [a for a in argv if a != "--verbose"]

    backend_arg = ""
    cwd = ""
    selector = ""
    pro_override: bool | None = None

    i = 0
    while i < len(filtered):
        a = filtered[i]
        if a == "--cwd":
            i += 1
            if i >= len(filtered):
                print("handoff open: --cwd requires a value", file=sys.stderr)
                sys.exit(2)
            cwd = filtered[i]
        elif a == "--backend":
            i += 1
            if i >= len(filtered):
                print("handoff open: --backend requires a value", file=sys.stderr)
                sys.exit(2)
            backend_arg = filtered[i]
        elif a.startswith("--backend="):
            backend_arg = a.split("=", 1)[1]
        elif a == "--pro":
            pro_override = True
        elif a in ("-h", "--help"):
            from ..main import usage
            usage()
            sys.exit(0)
        elif a.startswith("-"):
            print(f"handoff open: unknown option {a}", file=sys.stderr)
            sys.exit(2)
        else:
            if selector:
                print(f"handoff open: unexpected extra argument {a}", file=sys.stderr)
                sys.exit(2)
            selector = a
        i += 1

    conn = get_db()
    row = find_run(conn, selector or None)
    if not row:
        conn.close()
        print("handoff open: no run found", file=sys.stderr)
        sys.exit(1)

    session_id = row_value(row, "session_id", "") or row["uuid"]
    saved_backend = row_value(row, "backend", "") or ""
    row_cwd = row["cwd"]
    pro = _row_is_pro(row) if pro_override is None else pro_override

    if not cwd:
        cwd = row_cwd
    if not os.path.isdir(cwd):
        conn.close()
        print(f"handoff open: cwd not found: {cwd}", file=sys.stderr)
        sys.exit(2)

    if backend_arg and saved_backend and backend_arg != saved_backend:
        conn.close()
        print(
            f"handoff open: this conversation belongs to backend '{saved_backend}'; "
            f"it cannot be opened with --backend {backend_arg}.",
            file=sys.stderr,
        )
        sys.exit(2)
    backend_name = saved_backend or backend_arg or config.default_backend
    conn.close()

    _open_interactive(config, backend_name, session_id, cwd, pro, verbose=verbose)


def _row_is_pro(row) -> bool:
    info = parse_runtime_info(row_value(row, "runtime_info", ""))
    return bool(info.get("pro"))


def _open_interactive(
    config: Config,
    backend_name: str,
    session_id: str,
    cwd: str,
    pro: bool,
    verbose: bool = False,
):
    backend_cfg = config.get_backend(backend_name)
    if not backend_cfg:
        print(
            f"handoff: unknown backend '{backend_name}'. "
            f"Available: {', '.join(sorted(config.backends.keys()))}",
            file=sys.stderr,
        )
        sys.exit(2)

    model = resolve_backend_model(backend_cfg, pro)
    backend_cfg["_resolved_model"] = model
    backend_cfg["_system_prompt"] = config.system_prompt

    unset_keys, set_env = _resolved_open_env(backend_cfg, model)
    _apply_env(unset_keys, set_env)

    args = build_resume_args(
        backend_cfg,
        session_id,
        pro_model=backend_cfg.get("pro_model", ""),
    )

    if verbose:
        print(f"CMD: {format_shell_command(cwd, args, unset_keys, set_env)}", file=sys.stderr, flush=True)
    os.chdir(cwd)
    os.execvp(args[0], args)


def _resolved_open_env(backend_cfg: dict, model: str) -> tuple[list[str], dict[str, str]]:
    pro_model = backend_cfg.get("pro_model", "")
    unset_keys, set_env = resolved_backend_env(backend_cfg, model, pro_model)
    if backend_type(backend_cfg) != "claude":
        return unset_keys, set_env

    default_model = resolve_backend_model(backend_cfg, False)
    default_pro_model = resolve_backend_model(backend_cfg, True) or default_model

    base_env = {
        key: value
        for key, value in set_env.items()
        if key not in _OPEN_DROP_ENV_KEYS
        and key not in {
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        }
    }

    if os.path.expanduser(base_env.get("CLAUDE_CONFIG_DIR", "")) == os.path.expanduser("~/.claude"):
        base_env.pop("CLAUDE_CONFIG_DIR", None)

    ordered_env = {}
    if model:
        ordered_env["ANTHROPIC_MODEL"] = model
    if default_pro_model:
        ordered_env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = default_pro_model
    if default_model:
        ordered_env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = default_model
        ordered_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = default_model
    ordered_env.update(base_env)

    unset_keys = [key for key in unset_keys if key not in ordered_env]
    return unset_keys, ordered_env


def _apply_env(unset_keys: list[str], set_env: dict[str, str]) -> None:
    for key in unset_keys:
        os.environ.pop(key, None)
    for key in _OPEN_DROP_ENV_KEYS:
        os.environ.pop(key, None)
    for key, value in set_env.items():
        os.environ[key] = value
