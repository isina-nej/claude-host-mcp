# Claude Host MCP

Portable **local MCP server for Linux**. Gives Claude Desktop tools on real host, not just isolated VM/session.

Built for MCP Python SDK **v2** (`MCPServer`), stdio transport.

## Tools (24)

Core: `host_identity`, `system_summary`, `run_command`, `read_file`, `write_file`, `list_directory`
Files: `file_stat`, `file_search`, `file_grep`, `file_copy`, `file_move`, `file_delete`
Process: `process_list`, `process_kill`, `service_status` (user-scope systemd), `disk_usage`
Git: `git_status`, `git_log`, `git_diff`, `git_branch`, `git_commit` (no push)
Network: `http_fetch`, `network_check`, `download_file`

## Requirements

Linux, Python 3.10+, Claude Desktop with local MCP support. `uv` optional (fallback venv+pip).

## Install

```bash
git clone https://github.com/<you>/claude-host-mcp.git
cd claude-host-mcp
./install.sh
```

Installer: copies to `~/.local/share/claude-host-mcp`, isolated venv, installs `mcp>=2,<3`, detects `~/.config/Claude-3p/claude_desktop_config.json` or `~/.config/Claude/claude_desktop_config.json`, adds `host-system` server, enables 3P local-dev flags if profile present, backups before edit.

Restart Claude Desktop fully, new task/session. Test: `Use the host-system MCP tool host_identity.` Real hostname/user = working.

## Diagnostics

```bash
./doctor.sh
```

MCP logs: config/log dir, 3P Linux often `~/.config/Claude-3p/logs/`.

## Security

Runs as normal Linux user. `run_command` blocks: `sudo/su/pkexec`, shutdown/reboot, disk tools, raw `dd` to `/dev/*`, recursive rm of `/` or `$HOME`, root-wide chown/chmod, fork bomb.

Guardrail only, not sandbox. Shell powerful. Keep approval prompts on, review commands.

`process_kill` protects PID 1 and self. `file_delete` refuses configured roots. `git_commit` never pushes. `service_status` user-scope only.

### File roots

Defaults: read `$HOME`, `/etc`, `/var/log`; write `$HOME`. Override via `host-system` env:

```json
{
  "env": {
    "HOST_MCP_READ_ROOTS": "/home/alice:/etc:/var/log",
    "HOST_MCP_WRITE_ROOTS": "/home/alice/Documents",
    "HOST_MCP_MAX_TIMEOUT": "180",
    "HOST_MCP_MAX_OUTPUT": "50000",
    "HOST_MCP_MAX_DOWNLOAD": "20971520"
  }
}
```

Linux separator `:`.

## Custom config path

```bash
CLAUDE_DESKTOP_CONFIG="$HOME/some/path/claude_desktop_config.json" ./install.sh
```

## Uninstall

```bash
./uninstall.sh
```

Removes entry + runtime, backups config first.

## Dev

```python
from mcp.server import MCPServer
mcp = MCPServer("Linux Host System")
```

No stdout logs in stdio transport (stdout = JSON-RPC). Logging to stderr.

Smoke test:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); import claude_host_mcp.server; print('OK')"
```

## License

MIT
