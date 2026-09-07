# Changelog

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
