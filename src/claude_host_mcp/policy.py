"""Policy engine for claude-host-mcp: profiles, destructive classification, audit, rate limits.

Profiles (env HOST_MCP_PROFILE):
  safe      — read-only: run_command/shell blocked from writes, only read tools pass.
  developer — default. Full workspace read/write, git, process mgmt, network on.
  full      — developer + docker/package/service-restart style ops allowed.

Destructive classification drives ToolAnnotations.destructive_hint and the
audit trail. Anything destructive=True is the prompt surface: the client
asks the user before executing. Nothing here bypasses that; this module
only labels + logs.

Rate limits (env HOST_MCP_RATE_LIMIT, default "60/60"): max N tool calls
per window seconds, per tool family. Simple in-memory token bucket.

Audit log: JSONL at ~/.local/share/claude-host-mcp/audit.jsonl (override
HOST_MCP_AUDIT_FILE, empty disables). Every mutating tool logs
timestamp/tool/args-hint/ok/actor. No file contents logged — only paths
and sizes, so secrets never land in the trail.

ponytail: policy lives in env vars, not YAML files. Upgrade path: load
HOST_MCP_POLICY_FILE (yaml) with per-binary allow/deny lists and per-root
quotas when someone needs finer control than three profiles.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from collections import defaultdict, deque
from typing import Any

PROFILE = os.environ.get("HOST_MCP_PROFILE", "developer").lower()
if PROFILE not in ("safe", "developer", "full"):
    PROFILE = "developer"

_AUDIT_FILE = os.environ.get(
    "HOST_MCP_AUDIT_FILE",
    str(pathlib.Path.home() / ".local/share/claude-host-mcp/audit.jsonl"),
)
_audit_lock = threading.Lock()


def audit(tool: str, detail: dict[str, Any], ok: bool) -> None:
    """Append one JSONL audit record. Never raises, never logs file contents."""
    if not _AUDIT_FILE:
        return
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
              "tool": tool, "ok": ok, "profile": PROFILE, "detail": detail}
    try:
        path = pathlib.Path(_AUDIT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            with open(path, "a") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def audit_tail(count: int = 50) -> list[dict[str, Any]]:
    """Last N audit records, newest last."""
    count = max(1, min(int(count), 500))
    if not _AUDIT_FILE:
        return []
    try:
        lines = pathlib.Path(_AUDIT_FILE).read_text().splitlines()[-count:]
        return [json.loads(line) for line in lines if line.strip()]
    except Exception:
        return []


def audit_search(tool: str = "", ok: str = "", count: int = 50) -> list[dict[str, Any]]:
    """Filter audit trail by tool substring and ok true/false/any."""
    rows = audit_tail(500)
    needle = tool.lower()
    out = []
    for row in rows:
        if needle and needle not in str(row.get("tool", "")).lower():
            continue
        if ok in ("true", "false") and str(row.get("ok")).lower() != ok:
            continue
        out.append(row)
    return out[-max(1, min(int(count), 500)):]


# ---- rate limits: "N/seconds" per family ----

def _parse_limit(raw: str) -> tuple[int, int]:
    try:
        n, window = raw.split("/")
        return max(1, int(n)), max(1, int(window))
    except Exception:
        return 60, 60


_LIMIT_N, _LIMIT_WINDOW = _parse_limit(os.environ.get("HOST_MCP_RATE_LIMIT", "60/60"))
_hits: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()


def check_rate(family: str) -> tuple[bool, str]:
    """Token-bucket per tool family. Returns (allowed, reason)."""
    now = time.monotonic()
    with _rate_lock:
        bucket = _hits[family]
        while bucket and now - bucket[0] > _LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= _LIMIT_N:
            return False, (f"Rate limit: {len(bucket)} calls per {_LIMIT_WINDOW}s "
                           f"in family {family!r} exceeded.")
        bucket.append(now)
        return True, ""


# ---- path hardening: symlink-aware root check ----

def resolve_under(path: str, roots: list[pathlib.Path]) -> tuple[pathlib.Path | None, str]:
    """Resolve symlinks strictly, then check containment. Returns (resolved, error)."""
    try:
        target = pathlib.Path(path).expanduser()
        resolved = target.resolve(strict=False)
    except Exception as exc:
        return None, f"Cannot resolve path: {exc}"
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved, ""
        except ValueError:
            continue
    return None, f"Path is outside configured roots: {resolved}"


# ---- destructive classification: single source of truth ----

DESTRUCTIVE = frozenset({
    "file_delete", "file_move", "terminal_close", "terminal_signal",
    "process_kill", "job_cancel", "git_commit", "git_reset", "git_revert",
    "git_merge", "git_rebase", "git_checkout", "git_clean",
    "git_stash_pop", "git_stash_drop", "snapshot_restore",
    "docker_stop", "docker_restart", "docker_rm", "docker_exec",
    "package_install", "package_remove", "package_update",
    "service_restart", "memory_forget", "think_clear",
    "github_issue_create", "github_pr_create", "db_write",
    "browser_shot", "drive_get", "slack_send",
})

SAFE_READONLY = frozenset({
    "host_identity", "system_summary", "read_file", "list_directory",
    "file_stat", "file_search", "file_grep", "process_list",
    "service_status", "disk_usage", "git_status", "git_log", "git_diff",
    "git_branch", "http_fetch", "network_check", "terminal_read",
    "terminal_wait", "terminal_list", "job_status", "job_output",
    "job_wait", "job_list", "audit_log", "audit_search",
    "time_now", "time_convert", "time_zones", "memory_recall",
    "think_list", "fetch_text", "web_search", "wiki_search",
    "browser_fetch", "github_repo", "db_tables", "redis_get",
    "maps_geocode", "maps_directions", "drive_list", "slack_list",
    "db_status",
    "nine_status", "nine_models", "nine_combos", "nine_providers",
    "nine_usage", "nine_search",
})


def is_destructive(tool: str) -> bool:
    return tool in DESTRUCTIVE


def profile_allows(tool: str) -> tuple[bool, str]:
    """Gate by HOST_MCP_PROFILE. safe blocks everything not read-only."""
    if PROFILE == "full":
        return True, ""
    if PROFILE == "safe" and tool not in SAFE_READONLY:
        return False, f"Profile 'safe' blocks {tool}; switch HOST_MCP_PROFILE to developer/full."
    return True, ""


def register(mcp) -> None:
    """Register audit_log/audit_search tools on the given MCPServer."""
    import sys as _sys

    _impl_search = _sys.modules[__name__].audit_search  # impl; wrapper below shares the name
    from mcp.types import ToolAnnotations as _TA

    @mcp.tool(title="Audit log",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def audit_log(count: int = 50) -> list[dict[str, Any]]:
        """Return the last N mutating-tool audit records (paths and sizes only, never contents)."""
        return audit_tail(count)

    @mcp.tool(title="Search audit log",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def audit_search(tool: str = "", ok: str = "", count: int = 50) -> list[dict[str, Any]]:
        """Filter audit trail by tool name substring and ok true/false/any."""
        return _impl_search(tool, ok, count)
