# Changelog

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
