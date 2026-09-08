---
name: morning-diagnose
description: Diagnose a down service or dead port with diagnose-first flow. Use when user says service won't start, port down, "بالا نمیاد", or asks why localhost/service fails.
---

Run in order. Stop at first root cause.

1. `diagnose` with `service:NAME` or `host:port`. Read `failed_layers`.
2. Drill by layer: `service_status`, `journal_query` (service, lines 30), `process_list` (filter NAME), `port_owner` (port).
3. Before fix: `snapshot_create` on files you will touch.
4. Fix: `edit_file` with `dry_run=true` first, then real edit.
5. Verify: `git_diff`, rerun `journal_query`, `port_check`.
6. Fail path: `file_restore`, then `audit_log` (count 10) to log trail.

Never `process_kill` or `git_reset --hard` without explicit user confirm.
