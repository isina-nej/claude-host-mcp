#!/usr/bin/env bash
# macOS thin wrapper: same installer, macOS config path first.
set -euo pipefail
export CLAUDE_DESKTOP_CONFIG="${CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh"
