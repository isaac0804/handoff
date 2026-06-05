#!/usr/bin/env bash
# ds-cli online installer — clone (or update) ds-cli and link it, no manual clone needed.
#
#   curl -fsSL https://raw.githubusercontent.com/come2u/ds-cli/main/install-online.sh | bash
#
# Overridable via env:
#   DS_CLI_REPO   git URL to clone           (default: https://github.com/come2u/ds-cli.git)
#   DS_CLI_HOME   where to keep the checkout  (default: $XDG_DATA_HOME/ds-cli or ~/.local/share/ds-cli)
set -euo pipefail

REPO_URL="${DS_CLI_REPO:-https://github.com/come2u/ds-cli.git}"
DEST="${DS_CLI_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/ds-cli}"

command -v git     >/dev/null 2>&1 || { echo "ds-cli: git is required"     >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ds-cli: python3 is required" >&2; exit 1; }

if [ -d "$DEST/.git" ]; then
  echo "ds-cli: updating existing checkout at $DEST"
  git -C "$DEST" pull --ff-only
else
  echo "ds-cli: cloning into $DEST"
  mkdir -p "$(dirname "$DEST")"
  git clone --depth 1 "$REPO_URL" "$DEST"
fi

python3 -m pip install --user --quiet pyyaml || true

# --yes: this runs under `curl | bash` with no interactive stdin.
"$DEST/ds-cli" install --yes

echo
echo "ds-cli: installed. Make sure ~/bin is on your PATH, then run: ds-cli --help"
echo "ds-cli: update any time with: ds-cli update"
