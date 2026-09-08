# Changelog

## 0.7.0 - 2026-09-08

- 9router suite (14 tools + 2 resources): local AI gateway as first-class MCP.
- Discovery: nine_status/health+version (keyless), nine_models (349 routable), nine_combos (26 bundles), nine_providers (37 conns, no secrets), nine_usage (tokens/cost/providers).
- Chat: nine_chat single-shot with usage, nine_chat_stream SSE-concatenated, nine_fanout parallel multi-agent (max 6, judge/ensemble primitive).
- Media: nine_image (b64_json, FastImg auto-pick, verified 36KB live), nine_tts/nine_stt/nine_embeddings (shapes pass through), nine_video (speculative, 400s verified here).
- Web: nine_search via 9router searchapi (verified live weather results).
- Auth: NINEROUTER_API_KEY wins, else auto-reads first active key from ~/.9router sqlite. Probes handle 9router's padded-JSON + `data: [DONE]` suffix.
- Resources: nine://status, nine://models. nine_* reads in safe profile.

## 0.6.0 - 2026-09-08

- Everything works keyless by default; keys only upgrade quality/quotas (no more setup-error walls).
- web_search: default auto fans out to keyless DuckDuckGo HTML + Wikipedia + Instant Answer (deduped); backend param auto|wiki|duck|html|brave; Brave still wins when BRAVE_API_KEY is set. New wiki_search tool for reputable structured lookup.
- browser_fetch: works with zero config — local Chrome when installed, fetch_text fallback otherwise.
- github_*: public reads keyless (60/hr); token only raises quota + enables create.
- db_*: empty dsn auto-discovers local sqlite *.db; db_status probes sqlite/postgres/redis capability.
- redis_get: defaults to local 127.0.0.1:6379; docker:redis exec fallback when redis-cli is missing.
- drive_*: RCLONE_REMOTE optional; auto-picks when exactly one rclone remote exists.
- slack_*: SLACK_WEBHOOK_URL works for send without bot token; SLACK_BOT_TOKEN unlocks channels.
- safe profile extended: web_search, wiki_search, browser_fetch, github_repo, db_tables, redis_get, maps_*, drive_list, slack_list, db_status all read-only.

## 0.5.0 - 2026-09-07

- All-in-one suites: 114 tools (was 88), same 8 resources. No new pip dependencies.
- Mind (10): time_now/convert/zones via zoneinfo; memory_store/link/recall/forget via JSON graph (HOST_MCP_MEMORY_FILE); think/think_list/think_clear chain.
- Web data (4): fetch_text (boilerplate strip, <=3 redirects); web_search (duckduckgo keyless / brave keyed, default off); browser_fetch/shot via headless Chrome (opt-in HOST_MCP_BROWSER=chrome).
- Integrations (12): github_repo/issue/pr (GITHUB_TOKEN, create prompts); db_query/tables (sqlite stdlib, postgres psql, SELECT-first, writes need confirm+full); redis_get (REDIS_URL); maps_geocode/directions (google key else nominatim/straight-line); drive_list/get via rclone (RCLONE_REMOTE); slack_list/send (SLACK_BOT_TOKEN, send prompts).
- Secrets never logged; unconfigured integrations return setup errors, never crash.
- Tested stdio E2E on Pop!_OS: time/memory/think/fetch/maps green; gated suites return clean setup errors.

## 0.4.1 - 2026-09-07

- Fix wrapper/impl name shadowing in ops, policy, snapshots (RecursionError on system_snapshot, diagnose, audit_search, file_version, file_restore).
- Wrappers now resolve impls via sys.modules; verified live via stdio handshake: 88 tools, 5 static + 3 template resources, E2E calls green.

## 0.4.0 - 2026-09-07

- Agent control plane: 88 tools + 8 resources across terminal, jobs, files, git, ops, snapshots, policy.
- Terminal sessions: terminal_create/read/write/resize/signal/wait/close/list with cursor-based incremental output.
- Background jobs: job_start/status/output/wait/cancel/list with watchdog timeout and 100-entry history.
- Policy engine: HOST_MCP_PROFILE safe/developer/full, destructive classification, per-family rate limits (HOST_MCP_RATE_LIMIT).
- Audit trail: JSONL audit_log/audit_search, paths and sizes only, never file contents.
- Path hardening: symlink-aware resolve_under on write/move/delete paths.
- Developer files: edit_file (dry_run diff), apply_patch, head/tail, directory_tree, fd/rg find_files/search_text, fuzzy_find.
- Git toolkit: show/blame/tag/stash/checkout/reset/revert/merge/rebase/clean (dry_run default) + worktree create/list/remove; --hard/clean-exec require confirm=true.
- Linux ops: system_snapshot (cpu/mem/disk/load/temp/battery/gpu/net), journal_query, port_list/check/owner, diagnose host:port + service:.
- Docker: ps/logs/inspect/start/stop/restart/rm/exec (--privileged blocked), profile-gated.
- Packages: search/info on apt/dnf/pacman/brew/flatpak/snap; install/remove/update on apt/dnf/pacman/brew, developer/full only.
- Network detail: dns_lookup, interface_list, connection_list.
- Snapshots: snapshot_create/list/restore + file_version/file_restore, 50 slots, 200MB cap.
- Resources: system://summary/snapshot/ports, policy://current, audit://recent, process://{pid}, terminal://{session}, job://{job_id}.
- Approval policy carried over: only destructive tools prompt; rm-via-shell caveat documented.
- Verified live on Pop!_OS: 20-core i7, /proc ports, docker, apt, journal, diagnose layers.

## 0.3.0 - 2026-09-07

- Cross-platform: Linux, macOS, Windows. Same 24 tools, OS-adaptive internals.
- run_command: Bash on Linux/macOS, PowerShell on Windows.
- system_summary: df/free (Linux), vm_stat/sysctl (macOS), Get-ComputerInfo/Get-PSDrive (Windows).
- process_list: ps (Unix), tasklist (Windows). process_kill signal set adjusted per OS.
- service_status: systemd --user (Linux), launchctl filter (macOS), sc query (Windows).
- disk_usage: df/du (Unix), drive + walked dir size (Windows).
- file_search avoids Unix find.exe clash on Windows; file_grep gains pure-Python fallback.
- install.ps1 / uninstall.ps1 / doctor.ps1 for Windows; install-mac.sh wrapper; config auto-detect per OS.
- Blocklist extended: Restart-Computer/Stop-Computer, diskpart/Format-Volume/Clear-Disk/Remove-Partition, drive-root Remove-Item.

## 0.2.0 - 2026-09-07

- 24 tools: core 6 + files 6 + process 4 + git 5 + network 3.
- file_search/file_grep tolerate permission errors, still return matches.
- process_kill protects PID 1 and self; file_delete refuses roots.
- git_commit stages all, never pushes; service_status user-scope only.
- http_fetch/download_file capped (HOST_MCP_MAX_DOWNLOAD, default 20MB).

## 0.1.0 - 2026-09-07

- Initial portable Linux release.
- MCP Python SDK v2 via `MCPServer`.
- Claude Desktop and 3P config auto-detection.
- Safe-default file roots and command guardrails.
- Installer, uninstaller, doctor script.
