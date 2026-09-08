---
name: safe-deploy
description: Patch files with snapshot, test, verify, commit. Use when user asks to patch, fix, deploy, or says "درستش کن" for code changes.
---

Order is fixed.

1. `snapshot_create` on each target path.
2. `edit_file` with `dry_run=true`. Review diff. Then real edit.
3. `git_diff` to review. Run `job_start` for tests if present (`job_wait` after).
4. Green: `git_commit` with clear message. Never push.
5. Red: `file_restore` on touched paths. Then `audit_log` (count 10).
6. High risk (`git_reset --hard`, `git_clean` exec, `process_kill`): ask explicit confirm first.

Skip commit if tree clean.
