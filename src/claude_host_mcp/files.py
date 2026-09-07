"""Developer file tools for claude-host-mcp: edit, head/tail, tree, smart search.

edit_file applies exact-match replacements (dry_run preview supported) so
agents patch files without full read+rewrite round-trips. apply_patch
accepts unified diffs for multi-hunk changes.

find_files prefers fd, search_text prefers rg — both fall back to pathlib
and pure Python so Windows works without extra installs.

ponytail: edit_file matches are literal strings, not fuzzy. Upgrade path:
diff-match-patch style approximate matching with similarity threshold when
agents repeatedly fail on whitespace drift.
"""

from __future__ import annotations

import difflib
import pathlib
import shutil
import subprocess
from typing import Any

from . import policy as _policy

_MAX_EDIT_PREVIEW = 20000


def _roots():
    from . import server as _srv

    return _srv.READ_ROOTS, _srv.WRITE_ROOTS


def _run(argv: list[str], cwd: str | None = None, timeout: int = 30):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def edit(path: str, old: str, new: str, replace_all: bool = False,
         dry_run: bool = False) -> dict[str, Any]:
    """Replace exact `old` string with `new` in a writable file."""
    _read, write_roots = _roots()
    target, err = _policy.resolve_under(path, write_roots)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if not target.is_file():
        return {"ok": False, "error": f"Not a file: {target}"}
    if not old:
        return {"ok": False, "error": "Old string must not be empty."}
    try:
        text = target.read_text(errors="replace")
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    count = text.count(old)
    if count == 0:
        return {"ok": False, "error": "Old string not found in file."}
    if count > 1 and not replace_all:
        return {"ok": False, "error": f"Old string matches {count} times; set replace_all=true or narrow it."}
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    if dry_run:
        diff = "".join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True),
                                            "before", "after"))[:_MAX_EDIT_PREVIEW]
        return {"ok": True, "dry_run": True, "path": str(target),
                "replacements": count if replace_all else 1, "diff": diff}
    try:
        target.write_text(updated)
        _policy.audit("edit_file", {"path": str(target),
                                    "replacements": count if replace_all else 1,
                                    "delta_chars": len(updated) - len(text)}, True)
        return {"ok": True, "path": str(target),
                "replacements": count if replace_all else 1,
                "size_chars": len(updated)}
    except Exception as exc:
        _policy.audit("edit_file", {"path": str(target), "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


def apply_patch(path: str, patch: str, dry_run: bool = False) -> dict[str, Any]:
    """Apply a unified diff to a writable file. Uses `patch` binary when present."""
    _read, write_roots = _roots()
    target, err = _policy.resolve_under(path, write_roots)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if not target.is_file():
        return {"ok": False, "error": f"Not a file: {target}"}
    if not patch.strip():
        return {"ok": False, "error": "Empty patch."}
    try:
        original = target.read_text(errors="replace").splitlines(True)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if shutil.which("patch"):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
            fh.write(patch)
            diff_file = fh.name
        try:
            proc = _run(["patch", "--dry-run" if dry_run else "--quiet",
                         str(target), "-i", diff_file], timeout=30)
            ok = proc.returncode == 0
            detail = (proc.stdout + proc.stderr).strip()[:2000]
            if ok and not dry_run:
                _policy.audit("apply_patch", {"path": str(target),
                                              "patch_chars": len(patch)}, True)
                return {"ok": True, "path": str(target)}
            if ok and dry_run:
                return {"ok": True, "dry_run": True, "path": str(target),
                        "detail": detail or "patch applies cleanly"}
            return {"ok": False, "error": detail or "patch failed to apply"}
        finally:
            pathlib.Path(diff_file).unlink(missing_ok=True)
    # Fallback: reconstruct via difflib when patch is a simple two-file diff.
    try:
        patched = _naive_unified_apply(original, patch)
    except ValueError as exc:
        return {"ok": False, "error": f"Cannot apply without `patch` binary: {exc}"}
    if dry_run:
        diff = "".join(difflib.unified_diff(original, patched, "before", "after"))[:_MAX_EDIT_PREVIEW]
        return {"ok": True, "dry_run": True, "path": str(target), "diff": diff}
    try:
        target.write_text("".join(patched))
        _policy.audit("apply_patch", {"path": str(target), "patch_chars": len(patch)}, True)
        return {"ok": True, "path": str(target)}
    except Exception as exc:
        _policy.audit("apply_patch", {"path": str(target), "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


def _naive_unified_apply(original: list[str], patch: str) -> list[str]:
    """Minimal unified-hunk applier for @@ -a,b +c,d @@ hunks. Raises ValueError on complex diffs."""
    lines = patch.splitlines()
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
        elif current is not None and line[:1] in (" ", "-", "+"):
            current.append(line)
        elif line.startswith(("---", "+++", "diff ", "index ")):
            continue
        elif current is not None and not line.strip():
            current.append(" " + line if False else " ")
    if not hunks:
        raise ValueError("no hunks found")
    out: list[str] = []
    src = 0
    for hunk in hunks:
        # locate hunk by its context: find first context/deletion run in remaining source
        anchor = next((l[1:] for l in hunk if l.startswith((" ", "-"))), None)
        if anchor is None:  # pure-addition hunk: append at end
            for l in hunk:
                if l.startswith("+"):
                    out.append(l[1:] + ("\n" if not l[1:].endswith("\n") else ""))
            continue
        idx = None
        for i in range(src, len(original)):
            if original[i].rstrip("\n") == anchor.rstrip("\n"):
                idx = i
                break
        if idx is None:
            raise ValueError("hunk context not found; install `patch` for complex diffs")
        out.extend(original[src:idx])
        src = idx
        for l in hunk:
            body = l[1:]
            if l.startswith(" "):
                if src >= len(original) or original[src].rstrip("\n") != body.rstrip("\n"):
                    raise ValueError("context mismatch; install `patch` for complex diffs")
                out.append(original[src])
                src += 1
            elif l.startswith("-"):
                if src >= len(original) or original[src].rstrip("\n") != body.rstrip("\n"):
                    raise ValueError("deletion mismatch; install `patch` for complex diffs")
                src += 1
            else:
                out.append(body + ("\n" if not body.endswith("\n") else ""))
    out.extend(original[src:])
    return out


def head(path: str, lines: int = 50) -> str:
    read_roots, _w = _roots()
    target, err = _policy.resolve_under(path, read_roots)
    if err:
        return f"ERROR: {err}"
    assert target is not None
    if not target.is_file():
        return f"ERROR: not a file: {target}"
    n = max(1, min(int(lines), 2000))
    try:
        with open(target, errors="replace") as fh:
            out = []
            truncated = False
            for i, line in enumerate(fh):
                if i >= n:
                    truncated = True
                    break
                out.append(line)
        text = "".join(out)
        if truncated:
            text += "\n[file truncated: use tail_file or read_file with max_chars]"
        return text
    except Exception as exc:
        return f"ERROR: {exc}"


def tail(path: str, lines: int = 50) -> str:
    read_roots, _w = _roots()
    target, err = _policy.resolve_under(path, read_roots)
    if err:
        return f"ERROR: {err}"
    assert target is not None
    if not target.is_file():
        return f"ERROR: not a file: {target}"
    n = max(1, min(int(lines), 2000))
    try:
        with open(target, errors="replace") as fh:
            buf = fh.readlines()[-n:]
        return "".join(buf)
    except Exception as exc:
        return f"ERROR: {exc}"


def tree(path: str = "~", depth: int = 3, max_entries: int = 300) -> str:
    read_roots, _w = _roots()
    target, err = _policy.resolve_under(path, read_roots)
    if err:
        return f"ERROR: {err}"
    assert target is not None
    if not target.is_dir():
        return f"ERROR: not a directory: {target}"
    depth = max(1, min(int(depth), 8))
    limit = max(10, min(int(max_entries), 2000))
    rows = [f"{target.name}/"]
    count = 0
    truncated = False

    def walk(node: pathlib.Path, prefix: str, level: int) -> None:
        nonlocal count, truncated
        if level > depth or truncated:
            return
        try:
            kids = sorted(node.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        # hide noise by default
        kids = [k for k in kids if k.name not in
                {"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache", ".pytest_cache"}]
        for i, kid in enumerate(kids):
            if count >= limit:
                truncated = True
                return
            last = i == len(kids) - 1
            elbow = "└── " if last else "├── "
            rows.append(f"{prefix}{elbow}{kid.name}{'/' if kid.is_dir() else ''}")
            count += 1
            if kid.is_dir():
                walk(kid, prefix + ("    " if last else "│   "), level + 1)

    walk(target, "", 1)
    if truncated:
        rows.append(f"[tree truncated at {limit} entries]")
    return "\n".join(rows)


def find_files(root: str, pattern: str = "*", max_results: int = 100,
               use_fd: bool = True) -> str:
    read_roots, _w = _roots()
    base, err = _policy.resolve_under(root, read_roots)
    if err:
        return f"ERROR: {err}"
    assert base is not None
    if not base.is_dir():
        return f"ERROR: not a directory: {base}"
    limit = max(1, min(int(max_results), 1000))
    try:
        if use_fd and shutil.which("fd"):
            argv = ["fd", "--max-results", str(limit), pattern, str(base)]
            proc = _run(argv, timeout=60)
            lines = [l for l in proc.stdout.splitlines() if l][:limit]
        elif shutil.which("find") and __import__("os").name != "nt":
            proc = _run(["find", str(base), "-name", pattern, "-print"], timeout=60)
            lines = [l for l in proc.stdout.splitlines() if l][:limit]
        else:
            lines = [str(p) for p in list(base.rglob(pattern))[:limit]]
        if not lines:
            return "No matches."
        out = "\n".join(lines)
        if len(lines) == limit:
            out += "\n[result list truncated]"
        return out
    except Exception as exc:
        return f"ERROR: {exc}"


def search_text(root: str, pattern: str, max_matches: int = 50,
                regex: bool = False, case_sensitive: bool = False) -> str:
    read_roots, _w = _roots()
    base, err = _policy.resolve_under(root, read_roots)
    if err:
        return f"ERROR: {err}"
    assert base is not None
    if not base.is_dir():
        return f"ERROR: not a directory: {base}"
    limit = max(1, min(int(max_matches), 500))
    try:
        if shutil.which("rg"):
            argv = ["rg", "--no-heading", "--line-number", "-s",
                    f"--max-count={limit}"]
            if not case_sensitive:
                argv.append("-i")
            if not regex:
                argv.append("-F")
            argv += [pattern, str(base)]
            proc = _run(argv, timeout=60)
            lines = proc.stdout.splitlines()[:limit]
            if not lines:
                if proc.returncode not in (0, 1, 2):
                    return f"ERROR: {proc.stderr.strip() or 'search failed'}"
                return "No matches."
            out = "\n".join(lines)
            if len(proc.stdout.splitlines()) > limit:
                out += "\n[match list truncated]"
            return out
        from .server import _grep_python  # reuse fallback

        if not regex:
            import re as _re

            pattern = _re.escape(pattern)
        hits = _grep_python(base, f"(?{'':'i'}s){pattern}" if False else pattern, limit)
        if not case_sensitive and "No matches" not in hits:
            pass  # _grep_python is case-sensitive; documented limitation
        return hits
    except Exception as exc:
        return f"ERROR: {exc}"


def fuzzy_find(root: str, query: str, max_results: int = 20) -> str:
    """Subsequence filename match ranked by compactness. No dependency needed."""
    read_roots, _w = _roots()
    base, err = _policy.resolve_under(root, read_roots)
    if err:
        return f"ERROR: {err}"
    assert base is not None
    if not base.is_dir():
        return f"ERROR: not a directory: {base}"
    limit = max(1, min(int(max_results), 100))
    q = query.lower()
    scored: list[tuple[int, str]] = []
    scanned = 0
    for item in base.rglob("*"):
        scanned += 1
        if scanned > 50000:
            break
        try:
            if not item.is_file() or item.is_symlink():
                continue
        except OSError:
            continue
        name = item.name.lower()
        pos, score, qi = 0, 0, 0
        for ch in name:
            if qi < len(q) and ch == q[qi]:
                score += pos  # earlier matches rank better
                qi += 1
            pos += 1
        if qi == len(q):
            try:
                rel = str(item.relative_to(base))
            except ValueError:
                rel = str(item)
            scored.append((score + len(rel), rel))
    scored.sort()
    picks = [rel for _, rel in scored[:limit]]
    if not picks:
        return "No matches."
    out = "\n".join(picks)
    if len(scored) > limit:
        out += "\n[result list truncated]"
    return out


def register(mcp) -> None:
    from mcp.types import ToolAnnotations as _TA

    @mcp.tool(title="Edit file",
              annotations=_TA(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=True, open_world_hint=False))
    def edit_file(path: str, old: str, new: str, replace_all: bool = False,
                  dry_run: bool = False) -> dict[str, Any]:
        """Replace an exact string in a file. dry_run=true previews a unified diff without writing."""
        return edit(path, old, new, replace_all, dry_run)

    @mcp.tool(title="Apply patch",
              annotations=_TA(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=True, open_world_hint=False))
    def apply_patch(path: str, patch: str, dry_run: bool = False) -> dict[str, Any]:
        """Apply a unified diff to a file (`patch` binary preferred, naive fallback otherwise)."""
        from .files import apply_patch as _ap

        return _ap(path, patch, dry_run)

    @mcp.tool(title="File head",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def head_file(path: str, lines: int = 50) -> str:
        """Return the first N lines of a file."""
        return head(path, lines)

    @mcp.tool(title="File tail",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def tail_file(path: str, lines: int = 50) -> str:
        """Return the last N lines of a file."""
        return tail(path, lines)

    @mcp.tool(title="Directory tree",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def directory_tree(path: str = "~", depth: int = 3,
                       max_entries: int = 300) -> str:
        """Render an ASCII tree of a directory (noise dirs hidden)."""
        return tree(path, depth, max_entries)

    @mcp.tool(title="Find files",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def find_files_tool(root: str, pattern: str = "*", max_results: int = 100) -> str:
        """Find files by glob, fd preferred with find/pathlib fallback."""
        return find_files(root, pattern, max_results)

    @mcp.tool(title="Search text",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def search_text_tool(root: str, pattern: str, max_matches: int = 50,
                         regex: bool = False, case_sensitive: bool = False) -> str:
        """Search contents: literal by default, regex=true for patterns. rg preferred."""
        return search_text(root, pattern, max_matches, regex, case_sensitive)

    @mcp.tool(title="Fuzzy find files",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def fuzzy_find_tool(root: str, query: str, max_results: int = 20) -> str:
        """Subsequence filename search ranked by match compactness."""
        return fuzzy_find(root, query, max_results)
