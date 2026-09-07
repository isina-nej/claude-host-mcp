#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${HOST_MCP_INSTALL_DIR:-$HOME/.local/share/claude-host-mcp}"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || fail "python3 is required (Python 3.10+)."

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required")
PY

mkdir -p "$INSTALL_DIR"

# Copy the source tree so the installation is independent of the clone location.
cp -a "$PROJECT_DIR/pyproject.toml" "$PROJECT_DIR/README.md" "$INSTALL_DIR/"
rm -rf "$INSTALL_DIR/src"
cp -a "$PROJECT_DIR/src" "$INSTALL_DIR/"

if command -v uv >/dev/null 2>&1; then
  say "Using uv: $(command -v uv)"
  uv venv --python python3 "$INSTALL_DIR/.venv" >/dev/null
  uv pip install --python "$INSTALL_DIR/.venv/bin/python" -e "$INSTALL_DIR"
else
  say "uv not found; using python3 -m venv + pip"
  python3 -m venv "$INSTALL_DIR/.venv"
  "$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
fi

SERVER_BIN="$INSTALL_DIR/.venv/bin/claude-host-mcp"
[ -x "$SERVER_BIN" ] || fail "Installed MCP entry point not found: $SERVER_BIN"

CONFIG_PATH="${CLAUDE_DESKTOP_CONFIG:-}"
if [ -z "$CONFIG_PATH" ]; then
  CONFIG_PATH="$(python3 - <<'PY'
import json, pathlib
home = pathlib.Path.home()
candidates = [
    # macOS standard location first, then Linux variants.
    home / 'Library/Application Support/Claude/claude_desktop_config.json',
    home / '.config/Claude-3p/claude_desktop_config.json',
    home / '.config/Claude/claude_desktop_config.json',
]
for p in candidates:
    if p.exists():
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if p.parent.name == 'Claude-3p' and d.get('deploymentMode') == '3p':
            print(p); raise SystemExit
for p in candidates:
    if p.exists():
        print(p); raise SystemExit
import sys
if sys.platform == 'darwin':
    print(home / 'Library/Application Support/Claude/claude_desktop_config.json')
else:
    print(home / '.config/Claude/claude_desktop_config.json')
PY
)"
fi

mkdir -p "$(dirname "$CONFIG_PATH")"

HOST_MCP_SERVER_BIN="$SERVER_BIN" HOST_MCP_CONFIG="$CONFIG_PATH" python3 - <<'PY'
import datetime, json, os, pathlib, shutil
cfg = pathlib.Path(os.environ['HOST_MCP_CONFIG']).expanduser()
server = os.environ['HOST_MCP_SERVER_BIN']

if cfg.exists():
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = cfg.with_name(cfg.name + '.backup-host-mcp-' + stamp)
    shutil.copy2(cfg, backup)
    print('Config backup:', backup)
    data = json.loads(cfg.read_text())
else:
    data = {}

servers = data.setdefault('mcpServers', {})
servers['host-system'] = {
    'command': server,
    'args': [],
    'env': {
        'HOST_MCP_LOG_LEVEL': 'WARNING'
    }
}

cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
os.chmod(cfg, 0o600)
print('Claude config:', cfg)
print('MCP server: host-system')
print('Command:', server)
PY

# Claude Desktop 3P deployments may require local-dev MCP to be enabled in the
# active config-library profile. Do this only when that structure is present.
python3 - <<'PY'
import datetime, json, pathlib, shutil
root = pathlib.Path.home() / '.config/Claude-3p/configLibrary'
meta = root / '_meta.json'
if not meta.exists():
    raise SystemExit
try:
    m = json.loads(meta.read_text())
    aid = m.get('appliedId')
    cfg = root / f'{aid}.json' if aid else None
    if not cfg or not cfg.exists():
        raise SystemExit
    data = json.loads(cfg.read_text())
    changed = False
    for key in ('isDesktopExtensionEnabled', 'isDesktopExtensionDirectoryEnabled', 'isLocalDevMcpEnabled'):
        if data.get(key) is not True:
            data[key] = True
            changed = True
    if changed:
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = cfg.with_name(cfg.name + '.backup-host-mcp-' + stamp)
        shutil.copy2(cfg, backup)
        cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        print('3P profile enabled for local MCP:', cfg)
        print('3P profile backup:', backup)
except Exception as exc:
    print('WARNING: could not update optional Claude-3p profile:', exc)
PY

say ""
say "Installed successfully."
say "Restart Claude Desktop completely, then start a new Cowork/Code task."
say "Test prompt: Use the host-system MCP tool host_identity."
