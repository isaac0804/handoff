# Restore go/tail, wire Enter/G resume, refactor backend env + include-based config

## Context

The previous cleanup pass deleted the `go` and `tail` subcommands and flattened backend
env handling. The user now wants:

1. `go` and `tail` restored (they're useful — `go` resumes a past session in `claude`).
2. The TUI list page to expose resume directly (kept on the `G` key per the user's choice;
   `→`/`Enter` go to the scrollable detail view added last turn).
3. The backend env vars (`ANTHROPIC_*`, `CLAUDE_*`) collected under a single `env:` mapping
   in `backend_template`, so users can override any env var from their own config.
4. The config system flipped so `~/.ds-cli/config.yaml` **includes** the bundled
   `default_config.yaml` and ds-cli reads **only** the user file — letting YAML express the
   inheritance instead of Python merging two hardcoded files.

**Execution note:** implementation is to be carried out by a **sonnet worker** (sub-agent).

## Decisions (confirmed with user)

- **Include resolution:** user config writes an **absolute path** to the bundled defaults, e.g.
  `include: /Users/sam/dev/github/ds-cli/cli/default_config.yaml`. The path is computed at
  install time from the package location (`os.path.join(os.path.dirname(config.__file__),
  "default_config.yaml")`) and written into the generated config. The loader resolves absolute
  include paths as-is (relative paths still fall back to the including file's dir, then the
  package dir, for robustness).
- **TUI keys:** `↑↓/jk` move · `→`/`Enter` detail (scrollable) · `G` resume (exits ds-cli,
  exec `claude --resume <UUID>`) · `C` copy uuid · `q` quit.

## Changes

### 1. Restore deleted code (via `git show HEAD:<path>`, then adapt)

- `cli/commands/go.py` — restore verbatim. It imports `find_run`, `build_resume_args`,
  `set_backend_env`, `resolve_backend_model`. Works unchanged once those exist and
  `set_backend_env` reads the new `env` map.
- `cli/commands/tail.py` — restore verbatim. Imports `find_run`; uses `cclean`/`tail`/`grep`.
- `cli/core.py` — restore **`find_run`** only (not `resolve_jsonl`; it has no remaining
  callers). Source: `git show HEAD:cli/core.py`.
- `cli/backend.py` — restore **`build_resume_args`**. Source: `git show HEAD:cli/backend.py`.
  It reads `backend["resume_flags"]` (top-level, not env) — unchanged.

### 2. `cli/default_config.yaml` — group env + restore resume_flags

Move these keys under a new `env:` mapping inside `backend_template` (verbatim values):
`ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL`, `CLAUDE_CONFIG_DIR`, `ANTHROPIC_DEFAULT_OPUS_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`.
Keep `claude_command`, `pty`, `session_flags`, `session_id_flags` at the template top level.
Restore the `resume_flags` block (`--resume` / `{session_id}` / `--dangerously-skip-permissions`)
at the template top level.

```yaml
backend_template:
  env:
    ANTHROPIC_BASE_URL: "https://api.deepseek.com/anthropic"
    ANTHROPIC_MODEL: "{model}"
    CLAUDE_CONFIG_DIR: "{home}/.claude2"
    ANTHROPIC_DEFAULT_OPUS_MODEL: "{pro_model}"
    ANTHROPIC_DEFAULT_SONNET_MODEL: "{default_model}"
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "deepseek-v4-flash"
    CLAUDE_CODE_SUBAGENT_MODEL: "{default_model}"
  claude_command: "claude"
  pty: [...]            # unchanged
  session_flags: [...]  # unchanged
  session_id_flags: [...]
  resume_flags:
    - "--resume"
    - "{session_id}"
    - "--dangerously-skip-permissions"
```

### 3. `cli/backend.py` — `set_backend_env` reads the `env` map

- Replace the hardcoded `env_keys` loop with: iterate `backend.get("env", {})`, substitute
  placeholders with the existing `_resolve_env_val(val, ctx)`, set `os.environ[key]`.
  This auto-includes `ANTHROPIC_AUTH_TOKEN` (now under `env`) and any user-added env var.
- Keep the `CLAUDE_CONFIG_DIR` default-fallback block.
- `ensure_backend_token_ready`: read `backend.get("env", {}).get("ANTHROPIC_AUTH_TOKEN")`
  instead of the top-level key.
- `build_claude_args` / `build_resume_args` / `resolve_backend_model` unchanged (they read
  `claude_command`/`session_flags`/`resume_flags`/per-backend `default_model`, all top-level).

### 4. `cli/config.py` — include-based loading, single source = user config

- New loader `_load_with_includes(path, _seen=None)`:
  load YAML → pop `include` (str or list) → for each, resolve path (**absolute used as-is**;
  else relative to `path`'s dir, then `os.path.dirname(__file__)`), recurse, deep-merge in
  order, then deep-merge the current file's own keys on top. Guard include cycles via `_seen`.
- **Back-compat shim:** if a loaded file has no `include` key *and* the result lacks
  `backend_template`, implicitly include the bundled `default_config.yaml`. Keeps existing
  on-disk user configs (which have no `include:` line yet) working.
- `Config.__init__`: `_ensure_user_config_exists()` then
  `self._merged = _load_with_includes(user_config_path())`; drop the separate
  `self.defaults`/`self.user` fields and the direct `_DEFAULT_CONFIG_PATH` merge. Keep
  `_DEFAULT_CONFIG_PATH` as the include fallback target. `backends` property unchanged
  (`_deep_merge` already recurses into the nested `env`).
- The install-written config must include the **absolute path** to the bundled defaults.
  Since `_DEFAULT_USER_CONFIG` is currently a static string, change `write_default_user_config()`
  to format it with the computed absolute path
  (`os.path.join(os.path.dirname(__file__), "default_config.yaml")` — i.e. `_DEFAULT_CONFIG_PATH`).
  Nest the token under `env:`:

```yaml
# ds-cli user configuration
include: /abs/path/to/cli/default_config.yaml   # written at install time

default_backend: default
fast_backend: default

backends:
  default:
    description: "DeepSeek API"
    env:
      ANTHROPIC_AUTH_TOKEN: "<YOUR_TOKEN>"
```

- `get_config_paths()`: no external callers — leave returning user path (+ resolved includes
  if convenient); not load-bearing.

### 5. `cli/main.py` — re-add go/tail dispatch

- `known = {"run", "list", "go", "tail"}`; update the unknown-subcommand hint string.
- Import `cmd_go`, `cmd_tail`; add `elif` branches.
- `usage()`: keep `list` first and the English blurb; add `go` and `tail` lines, e.g.
  `ds-cli go   [<run-id|seq>]` (resume a past session in claude) and
  `ds-cli tail [<run-id|seq>]` (live-tail a run's stream).

### 6. `cli/tui.py` — restore G→resume in list mode

- Re-add to `toolbar_parts`: `("[G]", A_KEY), (" Resume  ", A_TOOLBAR),`.
- Re-add handler in list mode:
  `elif key in (ord("g"), ord("G")): return ("go", rows[selected]["run_id"])`.
- Restore docstring to "Returns ('go', run_id) or None."
- Leave `→`/`Enter`→detail and the detail-view scrolling (incl. detail-mode `g`/`G` =
  Top/End) exactly as-is — no conflict since they're different modes.

### 7. `cli/commands/list.py` — handle the resume action

```python
action = curses.wrapper(list_tui, rows, full_cwd)
if action and action[0] == "go":
    from .go import cmd_go
    cmd_go([str(action[1])], config)
```

## Verification

- `python -m py_compile cli/*.py cli/commands/*.py` → clean.
- `python -c "from cli.main import usage; usage()"` → lists `list` first plus `go`/`tail`.
- `python -m cli.main --help` and `python -m cli.main go -h` / `tail -h` → no import errors.
- Config: create a throwaway `/tmp/ds-cli-test/config.yaml` with `include: default_config.yaml`
  + a backend overriding `env.ANTHROPIC_BASE_URL`, point the loader at it (or temporarily set
  `HOME`), and assert `Config().backends["default"]["env"]` contains both the template vars and
  the override. Do **not** mutate the real `~/.ds-cli/config.yaml`; confirm the back-compat shim
  by loading a config with no `include:` and checking `backend_template` is present.
- `grep -rn "go\|tail\|find_run\|build_resume_args\|resume_flags" cli/` → confirm the restored
  symbols resolve and no stale references remain.
- Manual TUI smoke (`python -m cli.main list`): `G` exits ds-cli and runs
  `claude --resume <uuid>`; `→`/`Enter` open the scrollable detail; `←`/`Esc` return.
