# claude-host-mcp

![version](https://img.shields.io/badge/version-0.8.0-blue) ![tools](https://img.shields.io/badge/tools-146-brightgreen) ![resources](https://img.shields.io/badge/resources-10-blueviolet) ![platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey) ![license](https://img.shields.io/badge/license-MIT-yellow)

> **Give Claude Desktop hands on your real machine — safely.** One local MCP server (`host-system`) that lets Claude run shell, manage files, drive dev servers, inspect the system, use git, Docker, GitHub, databases, the web, and your local 9router AI gateway — on Linux, macOS, and Windows — with approvals, audit, and rollback.

![claude-host-mcp hero](assets/hero.png)

- 🖥️ **Your host, not a sandbox.** Claude sees your real hostname, files, processes, ports — not the VM.
- 🛡️ **Destructive = prompt.** Reads run free; deletes, kills, commits, restores, and external posts ask first.
- 🔌 **One server, zero new deps.** 146 tools + 10 resources over stdio. Vercel/Cloudflare/GitHub managed with link-and-wait auth. Python 3.10+, `mcp>=2,<3`. Integrations are credential-gated and fail clean without keys.

English | [فارسی](README.fa.md)

---

## 🚀 Install — pick your OS (60 seconds)

> **Install first, read later.** Same 146 tools everywhere — the code auto-adapts to your OS.
>
> Three installers, same result: an isolated venv at `~/.local/share/claude-host-mcp` and a `host-system` entry in your Claude config (backed up first).

<details open>
<summary><b>🐧 Linux / WSL</b></summary>

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
chmod +x install.sh install-mac.sh doctor.sh uninstall.sh
./install.sh
```

</details>

<details>
<summary><b>🍎 macOS</b></summary>

```bash
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
chmod +x install.sh install-mac.sh doctor.sh uninstall.sh
./install-mac.sh
```

Writes to `~/Library/Application Support/Claude/claude_desktop_config.json`.

</details>

<details>
<summary><b>🪟 Windows (PowerShell — use the <code>.ps1</code> scripts)</b></summary>

```powershell
git clone https://github.com/isina-nej/claude-host-mcp.git
cd claude-host-mcp
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

Writes to `%APPDATA%\Claude\claude_desktop_config.json`. Install dir: `%USERPROFILE%\.local\share\claude-host-mcp`.

</details>

**Then do all three, in order:**

1. **Fully quit Claude Desktop** (not just the window) and reopen it.
2. Open a **new** session/task.
3. Say:

```text
Use the host-system MCP tool host_identity.
```

Real hostname + your desktop user in the reply = wired to the host. If not, run `./doctor.sh` (or `.\doctor.ps1`) and see [Diagnostics](#-diagnostics).

**First taste after install:**

```text
system_snapshot  →  cpu, memory, disk, load, temps, battery, gpu, net in one call
diagnose "127.0.0.1:3000"  →  DNS → TCP → owner → HTTP → resources, with failed_layers
memory_store("my-project", "Next.js 15, pnpm, port 3000")  →  it remembers next session
```

Custom paths:

```bash
CLAUDE_DESKTOP_CONFIG="$HOME/path/claude_desktop_config.json" ./install.sh
HOST_MCP_INSTALL_DIR="$HOME/custom-dir" ./install.sh
```

| OS | Installer | Config location |
|---|---|---|
| Linux / WSL | `./install.sh` | `~/.config/Claude-3p/` or `~/.config/Claude/` |
| macOS | `./install-mac.sh` | `~/Library/Application Support/Claude/` |
| Windows | `.\install.ps1` | `%APPDATA%\Claude\` |

> Same 146 tools everywhere — the Python code checks `platform.system()` and adapts: `run_command` → Bash or PowerShell, `process_list` → `ps` or `tasklist`, `service_status` → `systemctl` / `launchctl` / `sc`, `port_list` → `ss` / `lsof` / `netstat` fallback.

---



## 📸 See it in action

| 🔍 `diagnose` finds the break | 🖥️ Terminals stay alive | ↩️ Snapshots = undo |
|---|---|---|
| ![diagnose demo](assets/demo-diagnose.gif) | ![terminal demo](assets/demo-terminal.gif) | ![snapshot demo](assets/demo-snapshot.gif) |
| One call runs DNS → TCP → owner → HTTP → resources and tells you which layer failed. | Dev servers and REPLs keep running between calls. Cursor reads, `wait` instead of polling. | `file_version` before risky edits, `file_restore` when tests fail. |

---



## 📑 Contents

- [Install — pick your OS](#-install--pick-your-os-60-seconds)
- [What it actually does](#-what-it-actually-does)
- [Tools (146, fourteen groups)](#-tools)
- [Resources (10)](#-resources)
- [Approval policy](#-approval-policy) · [Safety at a glance](#-safety-at-a-glance) · [Security](#-security)
- [Configuration](#-configuration) · [Diagnostics](#-diagnostics) · [Uninstall](#-uninstall) · [Development](#-development)

---



## 🧭 What it actually does

Claude Desktop runs agent tasks in a restricted sandbox/VM. This server is a **bridge out**: it runs on your real machine as your normal user and exposes typed tools over MCP stdio. Claude calls them; you approve the dangerous ones.

```
Claude Desktop ──stdio/JSON-RPC──▶ host-system MCP ──▶ your machine
                                        ├── shell + persistent terminals + background jobs
                                        ├── files + search + snapshots (undo)
                                        ├── git + worktrees
                                        ├── system: processes, journal, ports, diagnose, Docker, packages
                                        ├── mind: time, persistent memory, thinking chain
                                        ├── web + integrations: fetch, search, browser, GitHub, DBs, maps, drive, Slack
                                        ├── 9router AI: chat/fanout/image/search (multi-agent)
                                        ├── accounts: github/vercel/cloudflare link + manage
                                        └── guarded by: scoped roots · profiles · approvals · audit · rate limits
```

**Concrete things Claude can now do for you:**

- 🌡️ *"Why is my laptop hot?"* → `system_snapshot` shows CPU, temps, top processes in one call.
- 🔌 *"Why won't localhost:3000 load?"* → `diagnose` pinpoints the failed layer instead of guessing.
- 💻 *"Run my dev server and tell me when it's ready"* → `terminal_create` + `terminal_wait(pattern="ready")`, no polling loop.
- ✏️ *"Patch server.py, and undo it if tests fail"* → `file_version` → `edit_file` → `file_restore`.
- 🧠 *"Remember my stack"* → `memory_store` today, `memory_recall` next session.

**What it does NOT do:** no privilege escalation (`sudo`/`su` blocked), no shutdown/reboot, no disk formatting, no push on your behalf (`git_commit` never pushes), no silent exfiltration (secrets never hit the audit log; integrations need your keys).
## 🧰 Tools

146 tools in fourteen groups (verified live via stdio handshake). Only destructive tools prompt (see [Approval policy](#-approval-policy)).

![architecture](assets/architecture.png)

**Reading guide:** Core = everyday shell+files · Terminal+Jobs = long-running work without polling · Files/Git = semantic editing with rollback · Ops+Docker = observe then act · Mind+Web+Integrations = memory and outside world, all credential-gated · 9router = local AI (chat/fanout/image/search).

<details>
<summary><b>Show all 146 tools</b> — click to expand the full reference</summary>

### Core

| Tool | Description |
|---|---|
| `host_identity` | Hostname, `os` (Linux/Darwin/Windows), kernel, arch, user, home, server PID. Health-check tool. |
| `system_summary` | Linux: `hostname`/`uname`/`id`/`uptime`/`df`/`free`. macOS: `df`/`vm_stat`/`sysctl hw.memsize`. Windows: `hostname`/`whoami`/`Get-ComputerInfo`/`Get-PSDrive`. |
| `run_command` | Bash (`/bin/bash -lc`) on Linux/macOS, PowerShell on Windows. Args: `command`, `cwd`, `timeout_seconds`. Returns `exit_code`, `stdout`, `stderr`. |
| `read_file` | Text read inside readable roots. Args: `path`, `max_chars`. |
| `write_file` | Text write inside writable roots. Refuses overwrite unless `overwrite=true`. |
| `list_directory` | `DIR`/`FILE` listing inside readable roots. Args: `path`, `max_entries`. |

### Terminal sessions (persistent)

One shell process per session, kept alive across calls. For dev servers, REPLs, ssh — not one-shot commands.

| Tool | Description |
|---|---|
| `terminal_create` | Spawn shell (or run `command` interactively). Returns `session_id`, `pid`, `cwd`. |
| `terminal_read` | Incremental output since `cursor`. Returns new `cursor`. |
| `terminal_write` | Send keystrokes/commands to stdin. |
| `terminal_resize` | Store dimensions (metadata; no PTY ioctl yet). |
| `terminal_signal` | `INT`/`TERM`/`KILL` (`HUP` on Unix). Destructive — prompts. |
| `terminal_wait` | Block until regex `pattern`, exit, or `timeout_seconds`. Replaces polling loops. |
| `terminal_close` | Terminate session. Destructive — prompts. |
| `terminal_list` | Live sessions with pid, cwd, age, buffer size. |

### Background jobs

| Tool | Description |
|---|---|
| `job_start` | Launch command detached. Optional `timeout_seconds` watchdog kill. Returns `job_id`. |
| `job_status` | State, pid, exit code, buffer sizes. |
| `job_output` | Incremental `stdout`/`stderr` since `cursor`. |
| `job_wait` | Block until exit or timeout. Prefer over polling. |
| `job_cancel` | `TERM` then `KILL` after 5s. Destructive — prompts. |
| `job_list` | All jobs, or `running_only=true`. |

### Files

| Tool | Description |
|---|---|
| `file_stat` | Type, `size_bytes`, mtime, mode. |
| `file_search` | Recursive name match (`*.log`). Permission errors skipped, matches kept. |
| `file_grep` | Recursive content regex. `rg` preferred, `grep` on Unix, pure-Python fallback on Windows. Returns `file:line`. |
| `file_copy` | File/dir copy. Source readable, dest writable. |
| `file_move` | Move/rename. Both ends must be writable. Destructive — prompts. |
| `file_delete` | Delete file, or dir with `recursive=true`. Never deletes a configured root. Destructive — prompts. |
| `edit_file` | Exact-string replace (`old` → `new`). `dry_run=true` previews unified diff. Refuses ambiguous multi-match unless `replace_all=true`. |
| `apply_patch` | Unified diff apply (`patch` binary preferred, naive fallback). `dry_run` supported. |
| `head_file` | First N lines. |
| `tail_file` | Last N lines. |
| `directory_tree` | ASCII tree, `depth` + `max_entries`, noise dirs (`__pycache__`, `.git`, `.venv`, `node_modules`) hidden. |
| `find_files_tool` | Glob find, `fd` preferred, `find`/pathlib fallback. |
| `search_text_tool` | Literal (default) or `regex=true` content search, `rg` preferred. |
| `fuzzy_find_tool` | Subsequence filename ranking, dependency-free. |

### Process and system

| Tool | Description |
|---|---|
| `process_list` | `ps` sorted by CPU (Linux/macOS), `tasklist` on Windows. Args: `filter` substring, `limit`. |
| `process_kill` | Signal by PID (`HUP`/`INT`/`TERM`/`KILL`; no `HUP` on Windows). Protects PID 1 and self. Destructive — prompts. |
| `service_status` | User service status: systemd `--user` (Linux), `launchctl list` filtered (macOS), `sc query` (Windows). |
| `disk_usage` | `df -h` (Linux/macOS) or drive usage (Windows); plus `du -sh` / dir size for one allowed `path`. |
| `system_snapshot` | One-call cpu/memory/disk/load/temps/battery/gpu/network/uptime JSON. |
| `journal_query` | User journal tail with `service`/`priority`/`since` filters (macOS `log show`). |

### Git

| Tool | Description |
|---|---|
| `git_status` | Branch + `status --short --branch`. |
| `git_log` | Recent commits, short date format. Arg: `count`. |
| `git_diff` | Uncommitted diff + `--stat`. `staged=true` shows `--cached`. |
| `git_branch` | Local + remote branches (`branch -a -v`). |
| `git_commit` | `add -A` + `commit -m`. Refuses empty message, clean tree. Never pushes. Destructive — prompts. |
| `git_show` | Commit with stat, oneline. Read-only. |
| `git_blame` | Line-range blame of a tracked file. Read-only. |
| `git_tag` | `list` (read-only) / `create` / `delete`. |
| `git_stash` | `list` / `push` / `pop` / `drop`. pop/drop destructive. |
| `git_checkout` | Checkout (or `-b` create). Refuses on dirty tree. |
| `git_reset` | `--soft`/`--mixed`/`--hard`; `--hard` requires `confirm=true`. |
| `git_revert` | Inverse commit of a revision. |
| `git_merge` | Merge branch, conflict output on failure. |
| `git_rebase` | Rebase onto upstream; `abort`/`cont` for conflicts. |
| `git_clean` | `dry_run=true` default preview; execution requires `confirm=true`. |
| `git_worktree_create` | Isolated worktree under `.worktrees/` for agent work. |
| `git_worktree_list` | List worktrees. Read-only. |
| `git_worktree_remove` | Remove an agent worktree. |

### Network

| Tool | Description |
|---|---|
| `http_fetch` | `http(s)` GET, capped size. Returns `status`, `content_type`, `truncated`, `body`. |
| `network_check` | TCP reachability + `latency_ms`. Args: `host`, `port`, `timeout_seconds`. |
| `download_file` | `http(s)` to writable root, byte-capped. Aborts + cleans partial on overflow. |
| `dns_lookup` | Hostname → addresses. |
| `interface_list` | Interfaces with state + MAC. |
| `connection_list` | Active sockets via `ss`/`netstat`. |
| `port_list` | Listening sockets + owner pid/process (`/proc` on Linux, `ss`/`lsof` fallback). |
| `port_check` | TCP connect to `host:port` with latency. |
| `port_owner` | Owner of a listening port: pid, comm, cmdline, cwd. |
| `diagnose` | Layered diagnosis: `host:port`/`http(s)://` runs DNS→TCP→owner→HTTP→resources; `service:NAME` runs service→process→journal→ports. Returns `failed_layers`. |

![diagnose flow](assets/diagnose-flow.png)

### Docker

Requires the `docker` CLI. Mutations are profile-gated (developer/full) and prompt.

| Tool | Description |
|---|---|
| `docker_ps` | Containers (running default, `all=true` for all). |
| `docker_logs` | Tail container logs. |
| `docker_inspect` | State, image, ports, mounts. |
| `docker_start` / `docker_stop` / `docker_restart` / `docker_rm` | Lifecycle (10s stop timeout). |
| `docker_exec` | `sh -c` inside container. `--privileged` blocked. |

### Packages

Native manager auto-detected (apt/dnf/pacman/zypper/apk/brew/flatpak/snap). Search/info everywhere; mutations on apt/dnf/pacman/brew, developer/full profile only.

| Tool | Description |
|---|---|
| `package_search` | Search packages. |
| `package_info` | Package metadata. |
| `package_install` / `package_remove` | Install/remove. Prompt. |
| `package_update` | Refresh index. Prompt. |

### Mind: time, memory, thinking

No new dependencies. Mirrors the official time/memory/sequential-thinking servers with stdlib-only internals.

| Tool | Description |
|---|---|
| `time_now` | Current time in IANA timezone (default local). |
| `time_convert` | ISO datetime between timezones. |
| `time_zones` | List IANA zones, optional filter. |
| `memory_store` | Store observation on entity (persistent JSON graph). |
| `memory_link` | Typed relation between two entities. |
| `memory_recall` | Substring recall over entities/observations/relations. |
| `memory_forget` | Delete observation or whole entity. Destructive — prompts. |
| `think` | Record one reasoning step in a chain. |
| `think_list` | Return the thought chain. Read-only. |
| `think_clear` | Clear the chain. Destructive — prompts. |

### Web data: fetch, search, browser

| Tool | Description |
|---|---|
| `fetch_text` | Fetch URL → LLM-ready text (boilerplate stripped, ≤3 redirects). |
| `web_search` | Keyless-first fan-out (html+wiki+duck, deduped); `backend` auto\|wiki\|duck\|html\|brave; Brave when keyed. |
| `wiki_search` | Dedicated Wikipedia search. Keyless, reputable, structured. |
| `browser_fetch` | JS render via local Chrome when installed, fetch_text fallback otherwise. |
| `browser_shot` | Page screenshot PNG into writable roots. Opt-in; prompts. |

### Integrations: GitHub, databases, maps, drive, Slack

All credential-gated: without keys they return setup errors, never crash. Secrets never reach the audit log.

| Tool | Description |
|---|---|
| `github_repo` | Repo metadata keyless (60/hr); token raises quota. |
| `github_issue` | List/get/create issues. Create prompts. |
| `github_pr` | List/get/create PRs. Create prompts. |
| `db_query` | SELECT-first SQL; empty dsn auto-discovers local sqlite. Writes need `confirm=true` + full profile. |
| `db_status` | Keyless DB probe: sqlite files, postgres/redis reachability. |
| `db_tables` | List tables for a DSN. |
| `redis_get` | GET a key; defaults to local 127.0.0.1:6379; docker:redis fallback. |
| `maps_geocode` | Forward geocode (Google with key, else nominatim). |
| `maps_directions` | Routing (Google with key, else straight-line km). |
| `drive_list` | List `rclone` remote; auto-picks when one remote exists. |
| `drive_get` | Download remote file into writable roots. Prompts. |
| `slack_list` | List channels (`SLACK_BOT_TOKEN`; send also works via webhook). |
| `slack_send` | Post a message. Prompts. |

### 9router: local AI gateway

Your 9router (npm 0.5.69) at `127.0.0.1:20128` becomes 14 tools + 2 resources. Auth is automatic: `NINEROUTER_API_KEY` wins, else the first active key from `~/.9router/db/data.sqlite`. No new setup.

| Tool | Description |
|---|---|
| `nine_status` | Health + version. No key needed. Read-only. |
| `nine_models` | 349 routable combos + provider models. Read-only. |
| `nine_combos` | 26 bundles (`sina-pro`, `image`, `FastImg`...) with members. Read-only. |
| `nine_providers` | 37 connections, health only, no secrets. Read-only. |
| `nine_usage` | Requests, tokens, cost, providers. Read-only. |
| `nine_chat` | Ask any combo/model. Returns text + usage. |
| `nine_chat_stream` | SSE chat, concatenated text. |
| `nine_fanout` | Same prompt to N models in parallel (max 6). Judge/ensemble primitive. |
| `nine_image` | Generate images (`b64_json`). Auto-picks FastImg member. Verified live. |
| `nine_tts` | Text-to-speech. Shape passes through. |
| `nine_stt` | Speech-to-text from base64 audio. |
| `nine_embeddings` | Embed texts. Shape passes through. |
| `nine_search` | Web search via 9router `searchapi`. Keyless for you. |
| `nine_video` | Video generation. Speculative on most hosts. |

Resources: `nine://status`, `nine://models`.

> Multi-agent pattern: `nine_fanout(["sina-economy","sina-pro"], prompt)` → compare → `nine_chat(judge)` → decide. Fanout concurrency is capped at 6 to protect free-tier quotas.

### Snapshots and audit

| Tool | Description |
|---|---|
| `snapshot_create` | Copy file/dir into timestamped slot before risky ops. |
| `snapshot_list` | Slots with source + creation time. |
| `snapshot_restore` | Copy slot back. Destructive — prompts; `overwrite` required on clash. |
| `file_version` | One-call pre-edit file snapshot. |
| `file_restore` | Restore newest slot for a path. Destructive — prompts. |
| `audit_log` | Last N audit records (paths/sizes only, never contents). |
| `audit_search` | Filter by tool substring + ok true/false. |

### Accounts: link once, manage everything

One token store (`~/.local/share/claude-host-mcp/accounts/*.json`, chmod 600). Never pasted into audit. Resolution everywhere: explicit env → stored → CLI auto-login (`gh`, `vercel`) → keyless.

| Tool | Description |
|---|---|
| `accounts` | Linked providers with source + live status. Never returns secrets. Read-only. |
| `accounts_connect` | Start linking. Returns open-link URL + `request_id` (or `already:true`) **plus a live `login` sub-flow when available**: GitHub device code (`login.user_code` + `login.verification_uri`), Vercel localhost callback (`login.url`) when `HOST_MCP_VERCEL_CLIENT_ID` is set. |
| `accounts_wait` | Block until you authorize. Polls the live login flow (GitHub device, Vercel callback), then CLI-login state. Poll interval follows the provider (≥5s). |
| `accounts_complete` | Validate pasted token live, store chmod 600, close request. |
| `accounts_remove` | Delete a stored token. Prompts. CLI logins untouched. |
| `vercel_projects` | List projects (name, id, URL). Auto token. Read-only. |
| `vercel_deployments` | List deployments, optional project filter. Read-only. |
| `vercel_inspect` | Aliases, state, regions, creator. Read-only. |
| `vercel_logs` | Build/runtime event tail. Read-only. |
| `vercel_redeploy` | Rebuild a deployment. Prompts (creates live deploys). |
| `cloudflare_zones` | Zones (id, name, status, plan). Read-only. |
| `cloudflare_account` | First account id/name. Read-only. |
| `cloudflare_dns` | List DNS records for a zone. Read-only. |
| `cloudflare_dns_create` | Create a DNS record. Prompts. |
| `cloudflare_dns_delete` | Delete a DNS record by id. Prompts. |
| `cloudflare_purge` | Purge a zone cache. Prompts. |

> The flow Claude uses: sees `accounts` unlinked → `accounts_connect` returns a **link plus a live login** → you open it and approve → `accounts_wait` notices by itself. GitHub: open `login.verification_uri`, type `login.user_code`, approve (device flow, no app to create; override via `HOST_MCP_GITHUB_CLIENT_ID`). Vercel: with `HOST_MCP_VERCEL_CLIENT_ID` set, open `login.url` and approve on localhost (port via `HOST_MCP_VERCEL_REDIRECT_PORT`, default 8765); without it, `vercel login` in your terminal or a pasted token via `accounts_complete`. Cloudflare: token-only (no OAuth exists) — `accounts_complete` validates live.

</details>

## 📡 Resources

Live context without tool calls:

| URI | Content |
|---|---|
| `system://summary` | One-line identity, uptime, disk, memory. |
| `system://snapshot` | Full `system_snapshot` JSON. |
| `system://ports` | Listening-port table JSON. |
| `policy://current` | Profile, roots, caps, destructive set JSON. |
| `audit://recent` | Last 20 audit records JSON. |
| `process://{pid}` | ps row + cmdline + cwd JSON. |
| `terminal://{session}` | Buffer tail + alive state JSON. |
| `job://{job_id}` | Status + stdout/stderr tails JSON. |

## ✅ Approval policy

Only destructive tools prompt: `file_delete`, `file_move`, `terminal_close`, `terminal_signal`, `process_kill`, `job_cancel`, `git_commit`, `git_reset`, `git_revert`, `git_merge`, `git_rebase`, `git_checkout`, `git_clean` (exec), `git_tag` (create/delete), `git_stash` (pop/drop), `git_worktree_*` (create/remove), `snapshot_restore`, `file_restore`, `docker_*` (mutations), `package_*` (mutations), `memory_forget`, `think_clear`, `github_issue`/`github_pr` (create), `db_query` (writes), `browser_shot`, `drive_get`, `slack_send`, `accounts_remove`, `accounts_complete`, `vercel_redeploy`, `cloudflare_dns_create`, `cloudflare_dns_delete`, `cloudflare_purge`. Everything else — shell, reads, search, monitoring, journal, ports, diagnose — runs without approval friction.

> Caveat: deletion via shell (`rm` / `Remove-Item` inside `run_command`) is NOT blocked and does NOT prompt. Use `file_delete` for guarded deletes that request approval.

## 🛡️ Safety at a glance

![safety model](assets/safety.png)

Three profiles, one rule: **destructive = prompt**. `safe` passes read-only tools only; `developer` (default) adds workspace+git+process+network; `full` unlocks Docker/package/service-style ops. `reset --hard` and `clean` execution additionally need `confirm=true` in the call itself. Secrets (tokens, DSNs) never reach the audit log.

## 🔒 Security

Runs as your normal user. Anything that user can read/modify is reachable through tools.

Hard blocks in `run_command`: `sudo`/`su`/`pkexec`, shutdown/reboot/poweroff (`Restart-Computer`/`Stop-Computer` on Windows), disk tools (`mkfs`, `wipefs`, `fdisk`, `parted`, `diskpart`, `Format-Volume`, `Clear-Disk`), raw `dd of=/dev/*`, recursive `rm` of `/` or `$HOME` (drive-root `Remove-Item C:\` on Windows), root-wide `chown`/`chmod`, fork bombs.

Policy engine (`HOST_MCP_PROFILE`): `safe` = read-only tools pass, everything else blocked server-side; `developer` (default) = full workspace + git + process + network; `full` = developer + Docker/package/service-restart style ops. Destructive git ops (`reset --hard`, `clean` exec) additionally require `confirm=true` in the call. `docker_exec --privileged` always blocked. `process_kill` refuses PID 1 and self; `file_delete` refuses configured roots; `git_commit` never pushes; `service_status` user-scope only; `download_file`/`http_fetch` `http(s)` only, byte-capped.

Rate limits (`HOST_MCP_RATE_LIMIT`, default `60/60`): per-family call budget; excess calls fail with a rate-limit error instead of executing.

Audit (`~/.local/share/claude-host-mcp/audit.jsonl`, override `HOST_MCP_AUDIT_FILE`, empty disables): every mutating tool logs timestamp/tool/args-hint/ok. File contents never logged.

> Blocklist = guardrail, not sandbox. General shell access is inherently powerful. Tighten `*_ROOTS` to least privilege.

## Requirements

- Linux, macOS, or Windows; Python 3.10+
- Claude Desktop with local MCP support
- `uv` optional; installers fall back to `venv` + pip

## Configuration

Set under `host-system` → `env` in `claude_desktop_config.json`. Restart Claude Desktop after change.

| Variable | Default | Description |
|---|---|---|
| `HOST_MCP_PROFILE` | `developer` | `safe` (read-only) / `developer` / `full`. |
| `HOST_MCP_READ_ROOTS` | `$HOME:/etc:/var/log` (Linux/macOS), `$HOME` (Windows) | Readable roots (OS path separator). |
| `HOST_MCP_WRITE_ROOTS` | `$HOME` | Writable roots (OS path separator). |
| `HOST_MCP_MAX_OUTPUT` | `50000` | Output truncation cap, chars. |
| `HOST_MCP_MAX_TIMEOUT` | `180` | Max `run_command` timeout, seconds. |
| `HOST_MCP_MAX_DOWNLOAD` | `20971520` | Download/fetch cap, bytes (20 MB). |
| `HOST_MCP_AUDIT_FILE` | `~/.local/share/claude-host-mcp/audit.jsonl` | Audit trail path; empty disables. |
| `HOST_MCP_SNAPSHOT_DIR` | `~/.local/share/claude-host-mcp/snapshots` | Snapshot slot directory. |
| `HOST_MCP_RATE_LIMIT` | `60/60` | `N/seconds` per tool family. |
| `HOST_MCP_LOG_LEVEL` | `WARNING` | Python log level. |
| `HOST_MCP_MEMORY_FILE` | `~/.local/share/claude-host-mcp/memory.json` | Knowledge-graph file. |
| `HOST_MCP_WEB_SEARCH` | `auto` | Default backend: `auto` (keyless fan-out), `off` disables; `BRAVE_API_KEY` forces Brave. |
| `HOST_MCP_BROWSER` | `auto` | `auto` uses local Chrome if installed (else fetch_text fallback); `off` disables. |
| `GITHUB_TOKEN` / `GH_TOKEN` | _(unset)_ | Optional: raises quota + enables create; reads work keyless. |
| `POSTGRES_DSN` / `REDIS_URL` | _(unset)_ | Default DSNs for `db_*` / `redis_get`. |
| `GOOGLE_MAPS_API_KEY` | _(unset)_ | Google backend for `maps_*`; else nominatim/fallback. |
| `RCLONE_REMOTE` | _(unset)_ | e.g. `gdrive:` enables `drive_*`. |
| `SLACK_BOT_TOKEN` | _(unset)_ | Bot token for `slack_*`; `SLACK_WEBHOOK_URL` also enables send. |
| `NINEROUTER_API_KEY` | _(auto from `~/.9router`)_ | Optional override; else first active 9router key. |
| `NINEROUTER_BASE_URL` | `http://127.0.0.1:20128` | 9router gateway address. |
| `HOST_MCP_GITHUB_CLIENT_ID` | _(public default)_ | Override for GitHub device flow. Default is the public GitHub CLI app id (public client, no secret). |
| `HOST_MCP_VERCEL_CLIENT_ID` | _(unset)_ | Your own Vercel Integration client id. Enables localhost-callback login in `accounts_connect`. |
| `HOST_MCP_VERCEL_REDIRECT_PORT` | `8765` | Localhost callback port for Vercel OAuth. |

Example:

```json
{
  "mcpServers": {
    "host-system": {
      "command": "/home/alice/.local/share/claude-host-mcp/.venv/bin/claude-host-mcp",
      "args": [],
      "env": {
        "HOST_MCP_PROFILE": "developer",
        "HOST_MCP_READ_ROOTS": "/home/alice:/etc:/var/log",
        "HOST_MCP_WRITE_ROOTS": "/home/alice/Documents",
        "HOST_MCP_MAX_TIMEOUT": "180",
        "HOST_MCP_MAX_OUTPUT": "50000"
      }
    }
  }
}
```

## Diagnostics

```bash
./doctor.sh        # Linux / macOS
```

```powershell
.\doctor.ps1       # Windows
```

Checks OS, Python, venv entry point, MCP SDK import, registered config. MCP logs: Claude config/log dir; 3P Linux often `~/.config/Claude-3p/logs/`.

Common fixes:

- Full Claude Desktop restart (not just window reload), then a new session.
- If scripts won't run after `git clone`, re-apply `chmod +x install.sh install-mac.sh doctor.sh uninstall.sh`.
- On Windows, if PowerShell blocks scripts: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then `.\install.ps1`.
- If the wrong config got edited, re-run with `CLAUDE_DESKTOP_CONFIG` (Unix) or `-ClaudeConfig` (Windows) pointing at the right file.

## Uninstall

```bash
./uninstall.sh     # Linux / macOS
```

```powershell
.\uninstall.ps1    # Windows
```

Removes `host-system` entry (config backed up first) and installed runtime. Restart Claude Desktop.

## Development

Layout: `src/claude_host_mcp/` (`server.py`, `sessions.py`, `jobs.py`, `policy.py`, `files.py`, `gitx.py`, `ops.py`, `snapshots.py`, `resources.py`, `mind.py`, `webdata.py`, `ninerouter.py`), `skills/` (`morning-diagnose`, `safe-deploy`), `pyproject.toml` (hatchling), `install.sh`, `install-mac.sh`, `install.ps1`, `doctor.sh`, `doctor.ps1`, `uninstall.sh`, `uninstall.ps1`.

### Skills

Two prompt-only skills live in `skills/`. No code, just fixed tool order. Built from real `audit.jsonl` patterns (`edit_file` bursts, `web_search` loops, `nine_fanout` usage).

- `morning-diagnose`: `diagnose` → layer drill (`service_status`, `journal_query`, `process_list`, `port_owner`) → `snapshot_create` → `edit_file` (dry run first) → verify (`git_diff`, `journal_query`, `port_check`) → fail path `file_restore` + `audit_log`.
- `safe-deploy`: `snapshot_create` → `edit_file` (dry run) → `git_diff` + `job_start`/`job_wait` tests → `git_commit` (never push) → red path `file_restore` + `audit_log`.

Triggers: service down / "بالا نمیاد" → morning-diagnose. Patch/fix/deploy / "درستش کن" → safe-deploy.

Installers copy `skills/` into `~/.claude/skills/` automatically (`install.sh`, `install-mac.sh`, `install.ps1`). Skip with `./install.sh --skip-skills` or `.\install.ps1 -SkipSkills`. Override dest with `HOST_MCP_SKILLS_DIR`. Only skills carrying `installed-by-host-mcp` are removed on uninstall; your own same-named skills are backed up, never overwritten. `./doctor.sh` / `.\doctor.ps1` report installed vs missing.

```python
from mcp.server import MCPServer
mcp = MCPServer("Host System")
```

Rules: no stdout logging under stdio transport (stdout = JSON-RPC; log to stderr). Smoke test:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); import claude_host_mcp.server; print('OK')"
```

Full handshake check (tools + resources count):

```bash
PYTHONPATH=src python -m claude_host_mcp.server  # speak JSON-RPC on stdin; see CHANGELOG process
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Current: `0.7.0`.

## License

MIT — see [LICENSE](LICENSE).
