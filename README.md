# claude-host-mcp

> Local MCP server that gives Claude Desktop controlled access to the real Linux host — not just its isolated VM/session.

Built on MCP Python SDK **v2** (`MCPServer`), stdio transport. Runs as your normal Linux user.

English | [فارسی](README.fa.md)

## Why

Claude Desktop Cowork/Code tasks run in a restricted sandbox. This server bridges out: Claude calls 24 typed tools on the host for shell, files, processes, git, and network — with scoped file roots and dangerous-command guardrails.

## Tools

24 tools, five groups.

### Core

| Tool | Description |
|---|---|
| `host_identity` | Hostname, OS, kernel, arch, user, home, server PID. Health-check tool. |
| `system_summary` | `hostname`, `uname -a`, `id`, `uptime`, `df -h /`, `free -h` in one call. |
| `run_command` | Bash via `/bin/bash -lc` as desktop user. Args: `command`, `cwd`, `timeout_seconds`. Returns `exit_code`, `stdout`, `stderr`. |
| `read_file` | Text read inside readable roots. Args: `path`, `max_chars`. |
| `write_file` | Text write inside writable roots. Refuses overwrite unless `overwrite=true`. |
| `list_directory` | `DIR`/`FILE` listing inside readable roots. Args: `path`, `max_entries`. |

### Files

| Tool | Description |
|---|---|
| `file_stat` | Type, `size_bytes`, mtime, mode. |
| `file_search` | Recursive name match (`*.log`). Permission errors skipped, matches kept. |
| `file_grep` | Recursive content regex. `rg` preferred, `grep` fallback. Returns `file:line`. |
| `file_copy` | File/dir copy. Source readable, dest writable. |
| `file_move` | Move/rename. Both ends must be writable. |
| `file_delete` | Delete file, or dir with `recursive=true`. Never deletes a configured root. |

### Process and system

| Tool | Description |
|---|---|
| `process_list` | `ps` sorted by CPU. Args: `filter` substring, `limit`. |
| `process_kill` | Signal by PID (`HUP`/`INT`/`TERM`/`KILL`). Protects PID 1 and self. |
| `service_status` | User-scope systemd only: `systemctl --user status` + `journalctl --user`. |
| `disk_usage` | Always `df -h`; plus `du -sh` for one allowed `path`. |

### Git

| Tool | Description |
|---|---|
| `git_status` | Branch + `status --short --branch`. |
| `git_log` | Recent commits, short date format. Arg: `count`. |
| `git_diff` | Uncommitted diff + `--stat`. `staged=true` shows `--cached`. |
| `git_branch` | Local + remote branches (`branch -a -v`). |
| `git_commit` | `add -A` + `commit -m`. Refuses empty message, clean tree. Never pushes. |

### Network

| Tool | Description |
|---|---|
| `http_fetch` | `http(s)` GET, capped size. Returns `status`, `content_type`, `truncated`, `body`. |
| `network_check` | TCP reachability + `latency_ms`. Args: `host`, `port`, `timeout_seconds`. |
| `download_file` | `http(s)` to writable root, byte-capped. Aborts + cleans partial on overflow. |

## Requirements

- Linux, Python 3.10+
- Claude Desktop with local MCP support
- `uv` optional; installer falls back to `python3 -m venv` + pip

## Install

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
./install.sh
```

Installer does:

1. Copies source to `~/.local/share/claude-host-mcp`
2. Creates isolated venv, installs `mcp>=2,<3`
3. Detects `~/.config/Claude-3p/claude_desktop_config.json` or `~/.config/Claude/claude_desktop_config.json`
4. Registers `host-system` server (backs up config first, `chmod 600`)
5. Enables 3P local-dev MCP flags in active config-library profile, if present

Then fully restart Claude Desktop, open a new task/session.

Verify:

```text
Use the host-system MCP tool host_identity.
```

Real hostname + desktop user in reply = working.

Custom paths:

```bash
CLAUDE_DESKTOP_CONFIG="$HOME/path/claude_desktop_config.json" ./install.sh
HOST_MCP_INSTALL_DIR="$HOME/custom-dir" ./install.sh
```

## Configuration

Set under `host-system` → `env` in `claude_desktop_config.json`. Restart Claude Desktop after change.

| Variable | Default | Description |
|---|---|---|
| `HOST_MCP_READ_ROOTS` | `$HOME:/etc:/var/log` | Readable roots (`:`-separated). |
| `HOST_MCP_WRITE_ROOTS` | `$HOME` | Writable roots (`:`-separated). |
| `HOST_MCP_MAX_OUTPUT` | `50000` | Output truncation cap, chars. |
| `HOST_MCP_MAX_TIMEOUT` | `180` | Max `run_command` timeout, seconds. |
| `HOST_MCP_MAX_DOWNLOAD` | `20971520` | Download/fetch cap, bytes (20 MB). |
| `HOST_MCP_LOG_LEVEL` | `WARNING` | Python log level. |

Example:

```json
{
  "mcpServers": {
    "host-system": {
      "command": "/home/alice/.local/share/claude-host-mcp/.venv/bin/claude-host-mcp",
      "args": [],
      "env": {
        "HOST_MCP_READ_ROOTS": "/home/alice:/etc:/var/log",
        "HOST_MCP_WRITE_ROOTS": "/home/alice/Documents",
        "HOST_MCP_MAX_TIMEOUT": "180",
        "HOST_MCP_MAX_OUTPUT": "50000"
      }
    }
  }
}
```

## Security

Runs as your normal user. Anything that user can read/modify is reachable through tools.

Hard blocks in `run_command`: `sudo`/`su`/`pkexec`, shutdown/reboot/poweroff, disk tools (`mkfs`, `wipefs`, `fdisk`, `parted`, `cryptsetup`), raw `dd of=/dev/*`, recursive `rm` of `/` or `$HOME`, root-wide `chown`/`chmod`, fork bombs.

Additional limits: `process_kill` refuses PID 1 and self; `file_delete` refuses configured roots; `git_commit` never pushes; `service_status` user-scope only; `download_file`/`http_fetch` `http(s)` only, byte-capped.

> Blocklist = guardrail, not sandbox. General shell access is inherently powerful. Keep Claude tool-approval prompts enabled. Review commands before approving. Tighten `*_ROOTS` to least privilege.

## Diagnostics

```bash
./doctor.sh
```

Checks OS, Python, venv entry point, MCP SDK import, registered config. MCP logs: Claude config/log dir; 3P Linux often `~/.config/Claude-3p/logs/`.

Common fixes: full Claude Desktop restart (not just window reload), new session after install, `chmod +x install.sh doctor.sh uninstall.sh` after `git clone` on strict umasks.

## Uninstall

```bash
./uninstall.sh
```

Removes `host-system` entry (config backed up first) and installed runtime. Restart Claude Desktop.

## Development

Layout: `src/claude_host_mcp/server.py`, `src/claude_host_mcp/__init__.py`, `pyproject.toml` (hatchling), `install.sh`, `doctor.sh`, `uninstall.sh`.

```python
from mcp.server import MCPServer
mcp = MCPServer("Linux Host System")
```

Rules: no stdout logging under stdio transport (stdout = JSON-RPC; log to stderr). Smoke test:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); import claude_host_mcp.server; print('OK')"
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Current: `0.2.0`.

## License

MIT — see [LICENSE](LICENSE).
