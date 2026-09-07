#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${HOST_MCP_INSTALL_DIR:-$HOME/.local/share/claude-host-mcp}"

python3 - <<'PY'
import datetime, json, pathlib, shutil
home = pathlib.Path.home()
configs = [
    home / '.config/Claude-3p/claude_desktop_config.json',
    home / '.config/Claude/claude_desktop_config.json',
]
for cfg in configs:
    if not cfg.exists():
        continue
    try:
        data = json.loads(cfg.read_text())
    except Exception as exc:
        print(f'Skipping invalid config {cfg}: {exc}')
        continue
    servers = data.get('mcpServers')
    if not isinstance(servers, dict) or 'host-system' not in servers:
        continue
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = cfg.with_name(cfg.name + '.backup-uninstall-host-mcp-' + stamp)
    shutil.copy2(cfg, backup)
    del servers['host-system']
    if not servers:
        data.pop('mcpServers', None)
    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print('Removed host-system from:', cfg)
    print('Backup:', backup)
PY

rm -rf "$INSTALL_DIR"
echo "Removed $INSTALL_DIR"
echo "Restart Claude Desktop to finish uninstalling."
