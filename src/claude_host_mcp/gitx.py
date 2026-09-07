"""Extended git toolkit for claude-host-mcp: show/blame/tag/stash/checkout,
reset/revert/merge/rebase/clean plus worktrees.

Safety: read-only ops (show/blame/tag-list/diff) never prompt. Mutating
ops are destructive_hint=True so the client asks first. Hard resets,
forced pushes and clean -fd additionally require confirm=true in the
call itself — two layers: client approval AND explicit caller intent.
Never pushes unless the tool name says push.

ponytail: worktrees default under <repo>/.worktrees/<branch>. Upgrade
path: allow HOST_MCP_WORKTREE_ROOT override for centralized worktree
dirs when repos live on read-only mounts.
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess
from typing import Any

from . import policy as _policy

_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]{0,127}$")


def _run_git(repo: pathlib.Path, *args: str, timeout: int = 30):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _repo(path: str):
    from .server import _git_repo

    return _git_repo(path)


def _mutating(tool: str, family: str = "git"):
    from .server import _gate

    return _gate(tool, family)


def _check_branch(name: str) -> str:
    if not _BRANCH.match(name) or ".." in name or name.endswith((".lock", "/", ".")):
        return f"Invalid branch/tag name: {name!r}"
    return ""


def show(path: str, rev: str = "HEAD", max_chars: int = 20000) -> str:
    repo = _repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    if not rev.strip() or len(rev) > 256:
        return "ERROR: invalid revision."
    try:
        proc = _run_git(repo, "show", "--stat", "--oneline", rev, timeout=15)
        if proc.returncode != 0:
            return f"ERROR: {proc.stderr.strip() or 'git show failed'}"
        out = proc.stdout
        if len(out) > max_chars:
            out = out[:max_chars] + "\n[output truncated]"
        return out
    except Exception as exc:
        return f"ERROR: {exc}"


def blame(path: str, file: str, start: int = 0, count: int = 50) -> str:
    repo = _repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    rel = file.strip().strip("/")
    if not rel or ".." in rel.split("/"):
        return "ERROR: invalid file path."
    start = max(0, int(start))
    count = max(1, min(int(count), 500))
    try:
        proc = _run_git(repo, "blame", "-L", f"{start + 1},+{count}",
                        "--", rel, timeout=15)
        if proc.returncode != 0:
            return f"ERROR: {proc.stderr.strip() or 'git blame failed'}"
        return proc.stdout or "(empty)"
    except Exception as exc:
        return f"ERROR: {exc}"


def tag(path: str, action: str = "list", name: str = "", message: str = "") -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    action = action.lower()
    if action == "list":
        try:
            proc = _run_git(repo, "tag", "--list", timeout=15)
            tags = proc.stdout.split() if proc.returncode == 0 else []
            return {"ok": True, "tags": tags}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    if action not in ("create", "delete"):
        return {"ok": False, "error": "action must be list, create or delete."}
    if (blocked := _mutating("git_tag")) is not None:
        return blocked
    if (err := _check_branch(name)):
        return {"ok": False, "error": err}
    try:
        if action == "create":
            argv = ["tag", "-a", name, "-m", message or name] if message else ["tag", name]
            proc = _run_git(repo, *argv, timeout=15)
        else:
            proc = _run_git(repo, "tag", "-d", name, timeout=15)
        if proc.returncode != 0:
            _policy.audit("git_tag", {"repo": str(repo), "action": action,
                                      "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git tag failed"}
        _policy.audit("git_tag", {"repo": str(repo), "action": action, "name": name}, True)
        return {"ok": True, "repo": str(repo), "action": action, "name": name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def stash(path: str, action: str = "list", message: str = "",
          index: int = 0) -> dict[str, Any] | str:
    repo = _repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}" if action == "list" else repo
    action = action.lower()
    if action == "list":
        try:
            proc = _run_git(repo, "stash", "list", timeout=15)
            return proc.stdout or "(no stashes)"
        except Exception as exc:
            return f"ERROR: {exc}"
    if action not in ("push", "pop", "drop"):
        return {"ok": False, "error": "action must be list, push, pop or drop."}
    if (blocked := _mutating("git_stash_pop" if action in ("pop", "drop") else "git_stash")) is not None:
        return blocked
    try:
        if action == "push":
            argv = ["stash", "push", "-m", message] if message.strip() else ["stash", "push"]
            proc = _run_git(repo, *argv, timeout=30)
        elif action == "pop":
            proc = _run_git(repo, "stash", "pop", f"stash@{{{int(index)}}}", timeout=30)
        else:
            proc = _run_git(repo, "stash", "drop", f"stash@{{{int(index)}}}", timeout=15)
        if proc.returncode != 0:
            _policy.audit("git_stash", {"repo": str(repo), "action": action,
                                        "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git stash failed"}
        _policy.audit("git_stash", {"repo": str(repo), "action": action}, True)
        return {"ok": True, "repo": str(repo), "action": action,
                "output": (proc.stdout.strip() or "done")[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def checkout(path: str, branch: str, create: bool = False) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_checkout")) is not None:
        return blocked
    if (err := _check_branch(branch)):
        return {"ok": False, "error": err}
    try:
        status = _run_git(repo, "status", "--porcelain", timeout=15)
        if status.stdout.strip():
            return {"ok": False, "error": "Working tree is dirty; commit or stash first."}
        argv = ["checkout", "-b", branch] if create else ["checkout", branch]
        proc = _run_git(repo, *argv, timeout=30)
        if proc.returncode != 0:
            _policy.audit("git_checkout", {"repo": str(repo), "branch": branch,
                                           "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git checkout failed"}
        _policy.audit("git_checkout", {"repo": str(repo), "branch": branch,
                                       "create": create}, True)
        return {"ok": True, "repo": str(repo), "branch": branch}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def reset(path: str, mode: str = "--mixed", rev: str = "HEAD",
          confirm: bool = False) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if mode not in ("--soft", "--mixed", "--hard"):
        return {"ok": False, "error": "mode must be --soft, --mixed or --hard."}
    if (blocked := _mutating("git_reset")) is not None:
        return blocked
    if mode == "--hard" and not confirm:
        return {"ok": False, "error": "git reset --hard destroys work; set confirm=true to proceed."}
    try:
        proc = _run_git(repo, "reset", mode, rev, timeout=30)
        if proc.returncode != 0:
            _policy.audit("git_reset", {"repo": str(repo), "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git reset failed"}
        _policy.audit("git_reset", {"repo": str(repo), "mode": mode, "rev": rev}, True)
        return {"ok": True, "repo": str(repo), "mode": mode, "rev": rev,
                "output": proc.stdout.strip()[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def revert(path: str, rev: str, no_commit: bool = False) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_revert")) is not None:
        return blocked
    if not rev.strip() or len(rev) > 256:
        return {"ok": False, "error": "Invalid revision."}
    try:
        argv = ["revert", "--no-commit", rev] if no_commit else ["revert", "--no-edit", rev]
        proc = _run_git(repo, *argv, timeout=60)
        if proc.returncode != 0:
            _policy.audit("git_revert", {"repo": str(repo), "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or
                    "git revert failed (resolve conflicts manually)"}
        _policy.audit("git_revert", {"repo": str(repo), "rev": rev}, True)
        return {"ok": True, "repo": str(repo), "rev": rev,
                "output": proc.stdout.strip()[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def merge(path: str, branch: str, no_ff: bool = False) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_merge")) is not None:
        return blocked
    if (err := _check_branch(branch)):
        return {"ok": False, "error": err}
    try:
        argv = ["merge", "--no-ff", branch] if no_ff else ["merge", branch]
        proc = _run_git(repo, *argv, timeout=120)
        if proc.returncode != 0:
            _policy.audit("git_merge", {"repo": str(repo), "branch": branch,
                                        "error": proc.stderr.strip()[:500]}, False)
            return {"ok": False, "error": (proc.stdout + proc.stderr).strip()[:2000] or
                    "git merge failed (resolve conflicts manually)"}
        _policy.audit("git_merge", {"repo": str(repo), "branch": branch}, True)
        return {"ok": True, "repo": str(repo), "branch": branch,
                "output": proc.stdout.strip()[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def rebase(path: str, upstream: str, abort: bool = False,
           cont: bool = False) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_rebase")) is not None:
        return blocked
    try:
        if abort:
            proc = _run_git(repo, "rebase", "--abort", timeout=60)
        elif cont:
            proc = _run_git(repo, "rebase", "--continue", timeout=60)
        else:
            if (err := _check_branch(upstream)):
                return {"ok": False, "error": err}
            proc = _run_git(repo, "rebase", upstream, timeout=180)
        if proc.returncode != 0:
            _policy.audit("git_rebase", {"repo": str(repo),
                                         "error": (proc.stdout + proc.stderr).strip()[:500]}, False)
            return {"ok": False, "error": (proc.stdout + proc.stderr).strip()[:2000] or
                    "git rebase failed (resolve conflicts or --abort)"}
        _policy.audit("git_rebase", {"repo": str(repo), "upstream": upstream}, True)
        return {"ok": True, "repo": str(repo), "output": proc.stdout.strip()[:1000]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def clean(path: str, dirs: bool = False, confirm: bool = False,
          dry_run: bool = True) -> dict[str, Any]:
    repo = _repo(path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_clean")) is not None:
        return blocked
    if not dry_run and not confirm:
        return {"ok": False, "error": "git clean deletes untracked files; set confirm=true (dry_run=false) to proceed."}
    try:
        argv = ["clean", "-nd"] if dry_run else ["clean", "-fd" if dirs else "-f"]
        proc = _run_git(repo, *argv, timeout=30)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip() or "git clean failed"}
        if not dry_run:
            _policy.audit("git_clean", {"repo": str(repo), "dirs": dirs}, True)
        return {"ok": True, "repo": str(repo), "dry_run": dry_run,
                "output": proc.stdout.strip()[:2000] or "(nothing to clean)"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def worktree_create(repo_path: str, branch: str) -> dict[str, Any]:
    from .server import _git_repo as _gr

    repo = _gr(repo_path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_worktree")) is not None:
        return blocked
    if (err := _check_branch(branch)):
        return {"ok": False, "error": err}
    safe = branch.replace("/", "-")
    dest = repo / ".worktrees" / safe
    if dest.exists():
        return {"ok": False, "error": f"Worktree path exists: {dest}"}
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = _run_git(repo, "branch", "--list", branch, timeout=15)
        if existing.stdout.strip():
            proc = _run_git(repo, "worktree", "add", str(dest), branch, timeout=60)
        else:
            proc = _run_git(repo, "worktree", "add", "-b", branch, str(dest), timeout=60)
        if proc.returncode != 0:
            _policy.audit("git_worktree", {"repo": str(repo), "branch": branch,
                                           "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git worktree add failed"}
        _policy.audit("git_worktree", {"repo": str(repo), "branch": branch,
                                       "path": str(dest)}, True)
        return {"ok": True, "repo": str(repo), "branch": branch, "path": str(dest)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def worktree_list(repo_path: str) -> str:
    from .server import _git_repo as _gr

    repo = _gr(repo_path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    try:
        proc = _run_git(repo, "worktree", "list", "--verbose", timeout=15)
        if proc.returncode != 0:
            return f"ERROR: {proc.stderr.strip() or 'git worktree list failed'}"
        return proc.stdout or "(no worktrees)"
    except Exception as exc:
        return f"ERROR: {exc}"


def worktree_remove(repo_path: str, branch: str, force: bool = False) -> dict[str, Any]:
    from .server import _git_repo as _gr

    repo = _gr(repo_path)
    if isinstance(repo, dict):
        return repo
    if (blocked := _mutating("git_worktree")) is not None:
        return blocked
    safe = branch.replace("/", "-")
    dest = repo / ".worktrees" / safe
    try:
        argv = ["worktree", "remove", "--force", str(dest)] if force else \
            ["worktree", "remove", str(dest)]
        proc = _run_git(repo, *argv, timeout=60)
        if proc.returncode != 0:
            _policy.audit("git_worktree", {"repo": str(repo), "branch": branch,
                                           "error": proc.stderr.strip()}, False)
            return {"ok": False, "error": proc.stderr.strip() or "git worktree remove failed"}
        _policy.audit("git_worktree", {"repo": str(repo), "branch": branch,
                                       "removed": True}, True)
        return {"ok": True, "repo": str(repo), "branch": branch, "removed": str(dest)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def register(mcp) -> None:
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    @mcp.tool(title="Git show", annotations=_RO)
    def git_show(path: str, rev: str = "HEAD", max_chars: int = 20000) -> str:
        """Show a commit with stat (oneline). Read-only."""
        return show(path, rev, max_chars)

    @mcp.tool(title="Git blame", annotations=_RO)
    def git_blame(path: str, file: str, start: int = 0, count: int = 50) -> str:
        """Blame line ranges of a tracked file. Read-only."""
        return blame(path, file, start, count)

    @mcp.tool(title="Git tag", annotations=_MUT)
    def git_tag(path: str, action: str = "list", name: str = "",
                message: str = "") -> dict[str, Any]:
        """List/create/delete tags. list is read-only; create/delete prompt."""
        return tag(path, action, name, message)

    @mcp.tool(title="Git stash", annotations=_MUT)
    def git_stash(path: str, action: str = "list", message: str = "",
                  index: int = 0) -> dict[str, Any] | str:
        """Stash list/push/pop/drop. pop/drop are destructive."""
        return stash(path, action, message, index)

    @mcp.tool(title="Git checkout", annotations=_MUT)
    def git_checkout(path: str, branch: str, create: bool = False) -> dict[str, Any]:
        """Checkout branch (create with -b). Refuses on dirty tree."""
        return checkout(path, branch, create)

    @mcp.tool(title="Git reset", annotations=_MUT)
    def git_reset(path: str, mode: str = "--mixed", rev: str = "HEAD",
                  confirm: bool = False) -> dict[str, Any]:
        """Reset modes; --hard requires confirm=true."""
        return reset(path, mode, rev, confirm)

    @mcp.tool(title="Git revert", annotations=_MUT)
    def git_revert(path: str, rev: str, no_commit: bool = False) -> dict[str, Any]:
        """Revert a commit by creating an inverse commit."""
        return revert(path, rev, no_commit)

    @mcp.tool(title="Git merge", annotations=_MUT)
    def git_merge(path: str, branch: str, no_ff: bool = False) -> dict[str, Any]:
        """Merge a branch into the current one."""
        return merge(path, branch, no_ff)

    @mcp.tool(title="Git rebase", annotations=_MUT)
    def git_rebase(path: str, upstream: str = "", abort: bool = False,
                   cont: bool = False) -> dict[str, Any]:
        """Rebase onto upstream; abort/continue conflicted rebases."""
        return rebase(path, upstream, abort, cont)

    @mcp.tool(title="Git clean preview", annotations=_MUT)
    def git_clean(path: str, dirs: bool = False, confirm: bool = False,
                  dry_run: bool = True) -> dict[str, Any]:
        """Preview (dry_run) or execute clean; execution requires confirm=true."""
        return clean(path, dirs, confirm, dry_run)

    @mcp.tool(title="Git worktree create", annotations=_MUT)
    def git_worktree_create(path: str, branch: str) -> dict[str, Any]:
        """Create an isolated worktree under .worktrees/ for agent work."""
        return worktree_create(path, branch)

    @mcp.tool(title="Git worktree list", annotations=_RO)
    def git_worktree_list(path: str) -> str:
        """List worktrees of a repository. Read-only."""
        return worktree_list(path)

    @mcp.tool(title="Git worktree remove", annotations=_MUT)
    def git_worktree_remove(path: str, branch: str, force: bool = False) -> dict[str, Any]:
        """Remove an agent worktree created by git_worktree_create."""
        return worktree_remove(path, branch, force)


__all__ = ["register", "show", "blame", "tag", "stash", "checkout", "reset",
           "revert", "merge", "rebase", "clean", "worktree_create",
           "worktree_list", "worktree_remove"]
