#!/usr/bin/env bash
set -u

INSTALL_DIR="${HOST_MCP_INSTALL_DIR:-$HOME/.local/share/claude-host-mcp}"

echo '=== OS ==='
if [ -f /etc/os-release ]; then
  grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release 2>/dev/null || true
elif [ "$(uname)" = "Darwin" ]; then
  sw_vers 2>/dev/null || true
fi
uname -m

echo
echo '=== PYTHON ==='
python3 --version 2>&1 || true

echo
echo '=== INSTALLED SERVER ==='
ls -l "$INSTALL_DIR/.venv/bin/claude-host-mcp" 2>/dev/null || echo 'not installed'

if [ -x "$INSTALL_DIR/.venv/bin/python" ]; then
  echo
  echo '=== MCP SDK ==='
  "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import importlib.metadata
print('mcp', importlib.metadata.version('mcp'))
from mcp.server import MCPServer
print('MCPServer import OK')
PY
fi

echo
echo '=== CLAUDE CONFIGS ==='
python3 - <<'PY'
import json, pathlib, sys
home = pathlib.Path.home()
paths = [
    home / 'Library/Application Support/Claude/claude_desktop_config.json',  # macOS
    home / '.config/Claude-3p/claude_desktop_config.json',  # Linux 3P
    home / '.config/Claude/claude_desktop_config.json',  # Linux
]
if sys.platform == 'win32':
    appdata = pathlib.Path.home() / 'AppData/Roaming/Claude/claude_desktop_config.json'
    paths.insert(0, appdata)
for p in paths:
    if not p.exists():
        continue
    print(p)
    try:
        d = json.loads(p.read_text())
        s = d.get('mcpServers', {}).get('host-system')
        print('  host-system:', s if s else 'missing')
    except Exception as e:
        print('  invalid JSON:', e)
PY

echo
echo '=== CLAUDE DESKTOP ==='
command -v claude-desktop 2>/dev/null || true
claude-desktop --version 2>/dev/null || true
