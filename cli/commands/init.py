"""Interactive initializer for handoff."""

from __future__ import annotations

import os
import shutil
import sys


def _pkg_root() -> str:
    """Absolute path to the cli/ package directory."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _home_path(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def _short(path: str) -> str:
    """Replace the home directory with ~ for display."""
    home = os.path.expanduser("~")
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def _color(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def _planned_links():
    """Return (kind, src, dest) tuples for link files only (no config)."""
    skills_dir = os.path.join(_pkg_root(), "skills")
    return [
        ("hard link", os.path.join(skills_dir, "handoff-ds.toml"),
         _home_path(".codex", "agents", "handoff-ds.toml")),
        ("soft link", os.path.join(skills_dir, "handoff-ds", "SKILL.md"),
         _home_path(".claude", "skills", "handoff-ds", "SKILL.md")),
        ("soft link", os.path.join(skills_dir, "handoff-codex", "SKILL.md"),
         _home_path(".claude", "skills", "handoff-codex", "SKILL.md")),
        ("soft link", os.path.join(skills_dir, "handoff-opus", "SKILL.md"),
         _home_path(".claude", "skills", "handoff-opus", "SKILL.md")),
        ("soft link", os.path.join(skills_dir, "handoff-oc", "SKILL.md"),
         _home_path(".claude", "skills", "handoff-oc", "SKILL.md")),
    ]


def _print_plan():
    from ..config import user_config_path

    print(_color("1", "handoff initialization"))
    print("")

    links = _planned_links()
    print("The following will be created/updated:")
    for kind, src, dest in links:
        print(f"  {kind}: {_short(dest)} -> {_short(src)}")

    config_path = user_config_path()
    if os.path.isfile(config_path):
        print(f"\nConfig {_short(config_path)} already exists — will not be overwritten.")
    else:
        print(f"\nConfig {_short(config_path)} will be written.")

    print("")


def _confirm() -> bool:
    _print_plan()
    try:
        answer = input("Type Y to continue, anything else to exit: ").strip()
    except EOFError:
        answer = ""
    return answer.lower() == "y"


def _link_or_copy(link_fn, src: str, dest: str) -> str:
    """Try link_fn(src, dest); fall back to a plain copy if the platform refuses.

    Windows needs admin rights or Developer Mode for os.symlink, and os.link
    can fail across filesystem boundaries (e.g. the uv tool install dir vs.
    %USERPROFILE%) with WinError 17/1314. A copy loses "stays in sync with
    the installed package" but is the only option that always works; re-run
    `handoff init` after an upgrade to refresh copies.

    Returns "hard link" / "soft link" / "copy" for the caller to report.
    """
    try:
        link_fn(src, dest)
        return "hard link" if link_fn is os.link else "soft link"
    except OSError:
        shutil.copyfile(src, dest)
        return "copy"


def _create_links():
    """Create hard/soft links (or, on platforms that refuse those, plain copies)
    for agent and skill files from cli/skills/."""
    skills_dir = os.path.join(_pkg_root(), "skills")
    created = 0
    kinds_used = set()

    # Codex agent (hard link preferred)
    src_agent = os.path.join(skills_dir, "handoff-ds.toml")
    dest_agent = _home_path(".codex", "agents", "handoff-ds.toml")
    os.makedirs(os.path.dirname(dest_agent), exist_ok=True)
    if os.path.exists(dest_agent):
        os.remove(dest_agent)
    kinds_used.add(_link_or_copy(os.link, src_agent, dest_agent))
    created += 1

    # Claude Code skills (4 backends; soft link preferred)
    for skill_name in ("handoff-ds", "handoff-codex", "handoff-opus", "handoff-oc"):
        src_skill = os.path.join(skills_dir, skill_name, "SKILL.md")
        dest_skill_dir = _home_path(".claude", "skills", skill_name)
        dest_skill = os.path.join(dest_skill_dir, "SKILL.md")
        os.makedirs(dest_skill_dir, exist_ok=True)
        if os.path.lexists(dest_skill):
            os.remove(dest_skill)
        kinds_used.add(_link_or_copy(os.symlink, src_skill, dest_skill))
        created += 1

    print(f"+ Created {created} links/copies ({', '.join(sorted(kinds_used))})")


_OPENCODE_SAFE_AGENT = "handoff-safe"


def _opencode_agent_path() -> str:
    """Where the handoff-safe opencode agent must live for --agent to resolve.

    Matches opencode's own global-agent convention (XDG_CONFIG_HOME, which
    opencode applies even on Windows) — not part of this repo, so `handoff
    init` can only check for it, not create it.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME") or _home_path(".config")
    return os.path.join(config_home, "opencode", "agents", f"{_OPENCODE_SAFE_AGENT}.md")


def _check_opencode_agent():
    """Warn (never fail) if an opencode-type backend is configured but the
    handoff-safe agent it hardcodes via --agent isn't set up on this machine.

    Without it, `handoff run --backend <opencode-type-backend>` fails at
    launch with opencode's own "unknown agent" error — this just gives a
    clearer signal at init time instead of a confusing failure on first use.
    """
    from ..config import Config

    try:
        config = Config()
        uses_opencode = any(
            b.get("type") == "opencode" for b in config.backends.values()
        )
    except SystemExit:
        return  # config not fully set up yet — nothing to check
    if not uses_opencode:
        return

    agent_path = _opencode_agent_path()
    if os.path.isfile(agent_path):
        return

    print("")
    print(f"! opencode backend configured, but the '{_OPENCODE_SAFE_AGENT}' agent")
    print(f"  it requires (--agent {_OPENCODE_SAFE_AGENT}) was not found at:")
    print(f"    {_short(agent_path)}")
    print(
        "  Without it, `handoff run --backend <opencode backend>` will fail "
        "with opencode's"
    )
    print(
        "  own \"unknown agent\" error. This agent keeps unattended background "
        "dispatch from"
    )
    print(
        "  touching infra MCP servers (Supabase, Firebase, ...) even if a "
        "project's opencode"
    )
    print(
        "  config wires them up — create it once per machine before using "
        "this backend."
    )


def run_init(assume_yes: bool = False):
    if not assume_yes and not _confirm():
        print("handoff: initialization cancelled")
        sys.exit(1)

    print("")
    from ..config import user_config_path, write_default_user_config

    wrote_config = write_default_user_config()
    if wrote_config:
        print(f"+ Wrote {_short(user_config_path())}")
    else:
        print(f"  Config {_short(user_config_path())} already exists (skipped)")

    _create_links()
    _check_opencode_agent()

    readme_url = "https://github.com/dazuiba/handoff#configuration"

    print("")
    print("Next:")
    print(f"  1. Edit {_short(user_config_path())} and replace"
          f" ${{DEEPSEEK_API_KEY}} with your API key.")
    print(f"  2. For help, see {readme_url}")


def cmd_init(args):
    if args and args[0] in ("-h", "--help"):
        print("usage: handoff init [-y|--yes]")
        return
    assume_yes = False
    for arg in args:
        if arg in ("-y", "--yes"):
            assume_yes = True
        else:
            print(f"handoff: init: unexpected argument '{arg}'", file=sys.stderr)
            sys.exit(2)
    run_init(assume_yes=assume_yes)
