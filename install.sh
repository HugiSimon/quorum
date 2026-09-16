#!/bin/sh
# Installs quorum as a command, from nothing. macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/HugiSimon/quorum/master/install.sh | sh
#
# It installs uv if it is missing, then quorum as a tool: its own isolated environment,
# a `quorum` command in ~/.local/bin. Nothing is compiled.
set -e

REPO="${QUORUM_REPO:-https://github.com/HugiSimon/quorum}"

if ! command -v uv >/dev/null 2>&1; then
    echo "· uv is missing — installing it"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # The freshly installed uv is not on this shell's PATH yet.
    . "$HOME/.local/bin/env" 2>/dev/null || PATH="$HOME/.local/bin:$PATH"
fi

echo "· installing quorum from $REPO"
uv tool install --force "git+$REPO"
uv tool update-shell || true

echo
if command -v quorum >/dev/null 2>&1; then
    echo "quorum is installed. Go into a project and type: quorum"
else
    echo "quorum is installed in ~/.local/bin, which is not on your PATH yet."
    echo "Open a new terminal, or: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
echo "It needs an ACP agent: gemini --acp, or npx @zed-industries/claude-code-acp."
