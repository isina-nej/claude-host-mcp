from __future__ import annotations

import getpass
import logging
import os
import pathlib
import platform
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from . import policy as _policy

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("HOST_MCP_LOG_LEVEL", "WARNING"))

mcp = MCPServer("Host System")


def _gate(tool: str, family: str) -> dict[str, Any] | None:
    """Profile + rate gate. Returns error dict when blocked, None when allowed."""
    allowed, reason = _policy.profile_allows(tool)
    if not allowed:
        return {"ok": False, "error": reason}
    passed, reason = _policy.check_rate(family)
    if not passed:
        return {"ok": False, "error": reason}
    return None

SYSTEM = platform.system()  # Linux, Darwin, Windows, ...
IS_WINDOWS = os.name == "nt" or SYSTEM == "Windows"
IS_MAC = SYSTEM == "Darwin"

HOME = pathlib.Path.home().resolve()
MAX_OUTPUT = int(os.environ.get("HOST_MCP_MAX_OUTPUT", "50000"))
MAX_TIMEOUT = int(os.environ.get("HOST_MCP_MAX_TIMEOUT", "180"))
MAX_DOWNLOAD = int(os.environ.get("HOST_MCP_MAX_DOWNLOAD", "20971520"))  # 20 MB


def _default_read_roots() -> list[pathlib.Path]:
    if IS_WINDOWS:
        return [HOME]
    return [HOME, pathlib.Path("/etc"), pathlib.Path("/var/log")]


def _split_roots(value: str, defaults: list[pathlib.Path]) -> list[pathlib.Path]:
    if not value.strip():
        return [p.resolve() for p in defaults]
    roots: list[pathlib.Path] = []
    for item in value.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        roots.append(pathlib.Path(item).expanduser().resolve())
    return roots


READ_ROOTS = _split_roots(os.environ.get("HOST_MCP_READ_ROOTS", ""), _default_read_roots())
WRITE_ROOTS = _split_roots(os.environ.get("HOST_MCP_WRITE_ROOTS", ""), [HOME])

# This is a guardrail, not a security boundary. Raw shell access is powerful.
BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|[;&|]\s*)(sudo|su|pkexec)\b", re.I), "privilege escalation is disabled"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt|Restart-Computer|Stop-Computer)\b", re.I), "power control is disabled"),
    (re.compile(r"\b(mkfs(?:\.\w+)?|wipefs|fdisk|sfdisk|parted|cryptsetup|diskpart|Format-Volume|Clear-Disk|Remove-Partition)\b", re.I), "disk modification is disabled"),
    (re.compile(r"\bdd\b[^\n;]*\bof=/dev/", re.I), "raw disk writes are disabled"),
    (re.compile(r"(^|[;&|]\s*)rm\s+-[^\n;]*r[^\n;]*\s+/(?:\s|$|\*)", re.I), "recursive deletion of / is disabled"),
    (re.compile(r"(^|[;&|]\s*)rm\s+-[^\n;]*r[^\n;]*(?:~|\$HOME)(?:/|\s|$)", re.I), "recursive deletion of the home directory is disabled"),
    (re.compile(r"Remove-Item\b[^\n;]*[A-Z]:\\(?:\s|$)", re.I), "recursive deletion of a drive root is disabled"),
    (re.compile(r"\b(chown|chmod)\b[^\n;]*\s+/(?:\s|$)", re.I), "root-wide permission changes are disabled"),
    (re.compile(r":\s*\(\s*\)\s*\{.*:\s*\|\s*:\s*&\s*\}\s*;", re.I | re.S), "fork bomb blocked"),
]

SERVICE_NAME = re.compile(r"^[A-Za-z0-9@._:+-]{1,128}$")
HOST_NAME = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
SIGNALS = {"HUP": signal.SIGHUP, "INT": signal.SIGINT, "TERM": signal.SIGTERM, "KILL": signal.SIGKILL}
if IS_WINDOWS:
    # Windows has no SIGHUP; os.kill still accepts SIGINT/SIGTERM/SIGKILL names.
    SIGNALS.pop("HUP", None)


def _trim(text: str, limit: int | None = None) -> str:
    limit = limit or MAX_OUTPUT
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[output truncated]"


def _is_under(path: pathlib.Path, roots: list[pathlib.Path]) -> bool:
    path = path.resolve()
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _check_shell_command(command: str) -> None:
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(command):
            raise ValueError(f"Blocked by host-system safety policy: {reason}")


def _shell_argv(command: str) -> list[str]:
    if IS_WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-lc", command]


def _run(argv: list[str], cwd: pathlib.Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)


def _current_user() -> str:
    for key in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(key)
        if value:
            return value
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _dir_size(path: pathlib.Path) -> tuple[int, bool]:
    """Walk a directory tree, capped. Returns (bytes, truncated)."""
    total = 0
    files = 0
    truncated = False
    for root, _dirs, names in os.walk(path):
        for name in names:
            files += 1
            if files > 100000 or total > 2_000_000_000:
                return total, True
            try:
                total += (pathlib.Path(root) / name).stat().st_size
            except OSError:
                continue
    return total, truncated


def _git_repo(path: str) -> pathlib.Path | dict[str, Any]:
    repo = pathlib.Path(path).expanduser().resolve()
    if not _is_under(repo, READ_ROOTS):
        return {"ok": False, "error": f"Path is outside configured readable roots: {repo}"}
    if not repo.is_dir():
        return {"ok": False, "error": f"Not a directory: {repo}"}
    proc = _run(["git", "-C", str(repo), "rev-parse", "--git-dir"])
    if proc.returncode != 0:
        return {"ok": False, "error": f"Not a git repository: {repo}"}
    return repo


def _grep_python(base: pathlib.Path, pattern: str, limit: int) -> str:
    """Pure-Python content search fallback (used on Windows when rg/grep are missing)."""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"ERROR: invalid regex: {exc}"
    hits: list[str] = []
    files = 0
    for item in base.rglob("*"):
        if len(hits) >= limit:
            break
        if files > 20000:
            hits.append("[scan truncated: too many files]")
            break
        try:
            if item.is_symlink() or not item.is_file() or item.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        files += 1
        try:
            text = item.read_text(errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{item}:{lineno}:{line[:500]}")
                if len(hits) >= limit:
                    break
    if not hits:
        return "No matches."
    out = "\n".join(hits)
    if len(hits) >= limit:
        out += "\n[match list truncated]"
    return out


@mcp.tool(
    title="Host identity",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def host_identity() -> dict[str, Any]:
    """Return identity information for the real host running this MCP server."""
    return {
        "hostname": platform.node(),
        "os": SYSTEM,
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "user": _current_user(),
        "home": str(HOME),
        "pid": os.getpid(),
    }


@mcp.tool(
    title="System summary",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def system_summary() -> str:
    """Get a compact summary of the real host: identity, uptime, disk and memory."""
    chunks: list[str] = []

    def add(argv: list[str], timeout: int = 15) -> None:
        try:
            proc = _run(argv, timeout=timeout)
            chunks.append(f"$ {' '.join(argv)}\n{proc.stdout}{proc.stderr}")
        except Exception as exc:
            chunks.append(f"$ {' '.join(argv)}\nERROR: {exc}")

    if IS_WINDOWS:
        add(["hostname"])
        add(["whoami"])
        add(["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-ComputerInfo -Property OsName,OsVersion,OsArchitecture | Format-List | Out-String -Width 200"], 20)
        add(["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-PSDrive -PSProvider FileSystem | Format-Table | Out-String -Width 200"], 20)
    elif IS_MAC:
        for argv in (["hostname"], ["uname", "-a"], ["id"], ["uptime"],
                     ["df", "-h", "/"], ["vm_stat"], ["sysctl", "hw.memsize"]):
            add(argv)
    else:
        for argv in (["hostname"], ["uname", "-a"], ["id"], ["uptime"],
                     ["df", "-h", "/"], ["free", "-h"]):
            add(argv)
    return _trim("\n".join(chunks))


@mcp.tool(
    title="Run command on host",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def run_command(command: str, cwd: str = "", timeout_seconds: int = 60) -> dict[str, Any]:
    """Run a shell command on the real host as the current desktop user.

    Bash (`/bin/bash -lc`) on Linux/macOS, PowerShell on Windows.
    The server blocks privilege escalation and several obviously destructive system commands.
    Note: deletion via shell (rm / Remove-Item) is NOT blocked and does NOT prompt;
    use file_delete for guarded deletes that request approval.
    """
    if (blocked := _gate("run_command", "shell")) is not None:
        return blocked
    _check_shell_command(command)
    workdir = pathlib.Path(cwd).expanduser().resolve() if cwd else HOME
    if not workdir.is_dir():
        return {"ok": False, "error": f"Working directory does not exist: {workdir}"}

    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT))
    try:
        proc = subprocess.run(
            _shell_argv(command),
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": _trim(proc.stdout),
            "stderr": _trim(proc.stderr),
            "cwd": str(workdir),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"Timed out after {timeout}s",
            "stdout": _trim(exc.stdout or ""),
            "stderr": _trim(exc.stderr or ""),
            "cwd": str(workdir),
        }
    except Exception as exc:
        logger.exception("run_command failed")
        return {"ok": False, "error": str(exc), "cwd": str(workdir)}


@mcp.tool(
    title="Read host file",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def read_file(path: str, max_chars: int = 50000) -> str:
    """Read a text file from an allowed host path.

    Default readable roots are the home directory plus /etc and /var/log on Linux/macOS.
    Configure HOST_MCP_READ_ROOTS to change them.
    """
    target = pathlib.Path(path).expanduser().resolve()
    if not _is_under(target, READ_ROOTS):
        return f"ERROR: path is outside configured readable roots: {target}"
    if not target.is_file():
        return f"ERROR: not a file: {target}"
    try:
        text = target.read_text(errors="replace")
    except Exception as exc:
        return f"ERROR: {exc}"
    limit = max(100, min(int(max_chars), 200000))
    return _trim(text, limit)


@mcp.tool(
    title="Write host file",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def write_file(path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
    """Write a text file inside an allowed writable host root.

    Existing files are not replaced unless overwrite=true.
    Default writable root is the user's home directory.
    Configure HOST_MCP_WRITE_ROOTS to change it.
    """
    if (blocked := _gate("write_file", "files")) is not None:
        return blocked
    target, err = _policy.resolve_under(path, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if target.exists() and not overwrite:
        return {"ok": False, "error": "File exists; set overwrite=true to replace it."}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        _policy.audit("write_file", {"path": str(target), "chars": len(content),
                                     "overwrite": overwrite}, True)
        return {"ok": True, "path": str(target), "characters": len(content)}
    except Exception as exc:
        _policy.audit("write_file", {"path": path, "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="List host directory",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def list_directory(path: str = "~", max_entries: int = 500) -> str:
    """List an allowed directory on the real host."""
    target = pathlib.Path(path).expanduser().resolve()
    if not _is_under(target, READ_ROOTS):
        return f"ERROR: path is outside configured readable roots: {target}"
    if not target.is_dir():
        return f"ERROR: not a directory: {target}"
    try:
        rows: list[str] = []
        limit = max(1, min(int(max_entries), 5000))
        for index, item in enumerate(sorted(target.iterdir(), key=lambda p: p.name.lower())):
            if index >= limit:
                rows.append("[entry list truncated]")
                break
            kind = "DIR " if item.is_dir() else "FILE"
            rows.append(f"{kind}  {item.name}")
        return _trim("\n".join(rows))
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="File stat",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def file_stat(path: str) -> dict[str, Any]:
    """Return size, modification time and type for a file on the host."""
    target = pathlib.Path(path).expanduser().resolve()
    if not _is_under(target, READ_ROOTS):
        return {"ok": False, "error": f"Path is outside configured readable roots: {target}"}
    try:
        st = target.stat()
        kind = "dir" if target.is_dir() else "file" if target.is_file() else "other"
        return {
            "ok": True,
            "path": str(target),
            "type": kind,
            "size_bytes": st.st_size,
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
            "mode": oct(st.st_mode & 0o777),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="Search files by name",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def file_search(root: str, pattern: str, max_results: int = 100) -> str:
    """Recursively find files whose name matches a glob pattern (e.g. *.log)."""
    base = pathlib.Path(root).expanduser().resolve()
    if not _is_under(base, READ_ROOTS):
        return f"ERROR: path is outside configured readable roots: {base}"
    if not base.is_dir():
        return f"ERROR: not a directory: {base}"
    limit = max(1, min(int(max_results), 1000))
    try:
        # NOTE: Windows ships its own find.exe with different syntax, so only use
        # Unix find on non-Windows; elsewhere fall back to pathlib.
        if os.name != "nt" and shutil.which("find"):
            # find exits nonzero on permission errors; still use whatever stdout it produced.
            proc = _run(["find", str(base), "-name", pattern, "-print"], timeout=60)
            lines = [line for line in proc.stdout.splitlines() if line][:limit]
        else:
            lines = [str(p) for p in list(base.rglob(pattern))[:limit]]
        if not lines:
            err = proc.stderr.strip() if (os.name != "nt" and shutil.which("find")) else ""
            return f"No matches.{(' Errors: ' + _trim(err, 500)) if err else ''}"
        out = "\n".join(lines)
        if len(lines) == limit:
            out += "\n[result list truncated]"
        return _trim(out)
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Search file contents",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def file_grep(root: str, pattern: str, max_matches: int = 50) -> str:
    """Recursively search file contents for a regex pattern. Prefers ripgrep, falls back to grep or pure Python."""
    base = pathlib.Path(root).expanduser().resolve()
    if not _is_under(base, READ_ROOTS):
        return f"ERROR: path is outside configured readable roots: {base}"
    if not base.is_dir():
        return f"ERROR: not a directory: {base}"
    limit = max(1, min(int(max_matches), 500))
    try:
        if shutil.which("rg"):
            # rg returns 2 on permission errors; still use stdout. -s silences that noise.
            argv = ["rg", "--no-heading", "--line-number", "-s", f"--max-count={limit}", pattern, str(base)]
        elif os.name != "nt":
            argv = ["grep", "-rn", "-s", f"--max-count={limit}", "-e", pattern, str(base)]
        else:
            return _trim(_grep_python(base, pattern, limit))
        proc = _run(argv, timeout=60)
        lines = proc.stdout.splitlines()[:limit]
        if not lines:
            # rg/grep exit 1 on no match, 2 on real error; without stdout both look the same.
            if proc.returncode not in (0, 1, 2):
                return f"ERROR: {proc.stderr.strip() or 'search failed'}"
            return "No matches."
        out = "\n".join(lines)
        if len(proc.stdout.splitlines()) > limit:
            out += "\n[match list truncated]"
        return _trim(out)
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Copy host file",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def file_copy(source: str, dest: str, overwrite: bool = False) -> dict[str, Any]:
    """Copy a file or directory. Source must be readable, destination must be writable."""
    src = pathlib.Path(source).expanduser().resolve()
    dst = pathlib.Path(dest).expanduser().resolve()
    if not _is_under(src, READ_ROOTS):
        return {"ok": False, "error": f"Source is outside configured readable roots: {src}"}
    if not _is_under(dst, WRITE_ROOTS):
        return {"ok": False, "error": f"Destination is outside configured writable roots: {dst}"}
    if not src.exists():
        return {"ok": False, "error": f"Source does not exist: {src}"}
    if dst.exists() and not overwrite:
        return {"ok": False, "error": "Destination exists; set overwrite=true to replace it."}
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=overwrite)
        else:
            shutil.copy2(src, dst)
        return {"ok": True, "source": str(src), "dest": str(dst)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="Move host file",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def file_move(source: str, dest: str, overwrite: bool = False) -> dict[str, Any]:
    """Move or rename a file or directory inside writable roots."""
    if (blocked := _gate("file_move", "files")) is not None:
        return blocked
    src, err = _policy.resolve_under(source, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": f"Source: {err}"}
    dst, err = _policy.resolve_under(dest, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": f"Destination: {err}"}
    assert src is not None and dst is not None
    if not src.exists():
        return {"ok": False, "error": f"Source does not exist: {src}"}
    if dst.exists() and not overwrite:
        return {"ok": False, "error": "Destination exists; set overwrite=true to replace it."}
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        _policy.audit("file_move", {"source": str(src), "dest": str(dst)}, True)
        return {"ok": True, "source": str(src), "dest": str(dst)}
    except Exception as exc:
        _policy.audit("file_move", {"source": source, "dest": dest, "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="Delete host file",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def file_delete(path: str, recursive: bool = False) -> dict[str, Any]:
    """Delete a file, or a directory when recursive=true. Never deletes a configured root itself."""
    if (blocked := _gate("file_delete", "files")) is not None:
        return blocked
    target, err = _policy.resolve_under(path, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if target == HOME or target in WRITE_ROOTS:
        return {"ok": False, "error": "Refusing to delete a configured root directory."}
    if not target.exists() and not target.is_symlink():
        return {"ok": False, "error": f"Path does not exist: {target}"}
    try:
        if target.is_dir() and not target.is_symlink():
            if not recursive:
                return {"ok": False, "error": "Directory; set recursive=true to delete it."}
            shutil.rmtree(target)
        else:
            target.unlink()
        _policy.audit("file_delete", {"path": str(target), "recursive": recursive}, True)
        return {"ok": True, "deleted": str(target)}
    except Exception as exc:
        _policy.audit("file_delete", {"path": path, "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="List processes",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def process_list(filter: str = "", limit: int = 30) -> str:
    """List host processes (ps sorted by CPU on Linux/macOS, tasklist on Windows), optionally filtered by a substring."""
    count = max(1, min(int(limit), 200))
    try:
        if IS_WINDOWS:
            if not shutil.which("tasklist"):
                return "ERROR: tasklist is not available on this host."
            proc = _run(["tasklist", "/FO", "TABLE"], timeout=15)
            if proc.returncode != 0:
                return f"ERROR: {proc.stderr.strip() or 'tasklist failed'}"
        else:
            proc = _run(
                ["ps", "-eo", "pid,ppid,user,%cpu,%mem,etime,comm", "--sort=-%cpu"],
                timeout=15,
            )
            if proc.returncode != 0:
                return f"ERROR: {proc.stderr.strip() or 'ps failed'}"
        lines = proc.stdout.splitlines()
        header, rows = (lines[0], lines[1:]) if lines else ("", [])
        if filter:
            needle = filter.lower()
            rows = [row for row in rows if needle in row.lower()]
        return _trim("\n".join([header, *rows[:count]]))
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Kill process",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def process_kill(pid: int, signal_name: str = "TERM") -> dict[str, Any]:
    """Send a signal to a host process. PID 1 and the MCP server itself are protected."""
    if (blocked := _gate("process_kill", "process")) is not None:
        return blocked
    name = signal_name.upper()
    if name not in SIGNALS:
        return {"ok": False, "error": f"Unknown signal {signal_name!r}; use one of {sorted(SIGNALS)}."}
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"Invalid pid: {pid!r}"}
    if pid <= 1:
        return {"ok": False, "error": "Refusing to signal PID 1."}
    if pid == os.getpid():
        return {"ok": False, "error": "Refusing to kill the MCP server itself."}
    try:
        os.kill(pid, SIGNALS[name])
        _policy.audit("process_kill", {"pid": pid, "signal": name}, True)
        return {"ok": True, "pid": pid, "signal": name}
    except ProcessLookupError:
        return {"ok": False, "error": f"No such process: {pid}"}
    except PermissionError:
        _policy.audit("process_kill", {"pid": pid, "signal": name, "error": "denied"}, False)
        return {"ok": False, "error": f"Permission denied for PID {pid}."}
    except Exception as exc:
        _policy.audit("process_kill", {"pid": pid, "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="User service status",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def service_status(name: str, log_lines: int = 20) -> str:
    """Show status of a user-scope service: systemd --user on Linux, launchctl on macOS, sc query on Windows."""
    if not SERVICE_NAME.match(name):
        return f"ERROR: invalid service name: {name!r}"
    try:
        if IS_WINDOWS:
            if not shutil.which("sc"):
                return "ERROR: sc is not available on this host."
            proc = _run(["sc", "query", name], timeout=15)
            return _trim(f"$ sc query {name}\n{proc.stdout}{proc.stderr}")
        if IS_MAC:
            if not shutil.which("launchctl"):
                return "ERROR: launchctl is not available on this host."
            proc = _run(["launchctl", "list"], timeout=15)
            if proc.returncode != 0:
                return f"ERROR: {proc.stderr.strip() or 'launchctl failed'}"
            needle = name.lower()
            matches = [line for line in proc.stdout.splitlines()
                       if needle in line.lower() or line.strip().startswith("PID")]
            if not matches:
                return f"No matching launchd service for {name!r}."
            return _trim("$ launchctl list (filtered)\n" + "\n".join(matches)
                         + "\n\nFor logs use: log show --predicate "
                           f"'process == \"{name}\"' --last 20m")
        if not shutil.which("systemctl"):
            return "ERROR: systemctl is not available on this host."
        lines = max(0, min(int(log_lines), 200))
        chunks = []
        for argv in (
            ["systemctl", "--user", "status", name, "--no-pager"],
            ["journalctl", "--user", "-u", name, "-n", str(lines), "--no-pager"],
        ):
            try:
                proc = _run(argv, timeout=15)
                chunks.append(f"$ {shlex.join(argv)}\n{proc.stdout}{proc.stderr}")
            except Exception as exc:
                chunks.append(f"$ {shlex.join(argv)}\nERROR: {exc}")
        return _trim("\n".join(chunks))
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Disk usage",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def disk_usage(path: str = "") -> str:
    """Show filesystem usage (df on Linux/macOS, drive usage on Windows) plus size of one allowed path."""
    chunks = []
    try:
        if IS_WINDOWS:
            total, used, free = shutil.disk_usage(HOME.anchor)
            chunks.append(f"Drive {HOME.anchor} total={_human(total)} used={_human(used)} free={_human(free)}")
        else:
            proc = _run(["df", "-h"], timeout=15)
            chunks.append(f"$ df -h\n{proc.stdout}{proc.stderr}")
    except Exception as exc:
        chunks.append(f"$ disk usage\nERROR: {exc}")
    if path:
        target = pathlib.Path(path).expanduser().resolve()
        if not _is_under(target, READ_ROOTS):
            chunks.append(f"Path {path}\nERROR: path is outside configured readable roots: {target}")
        elif not target.exists():
            chunks.append(f"Path {path}\nERROR: path does not exist: {target}")
        elif IS_WINDOWS:
            size, truncated = _dir_size(target) if target.is_dir() else (target.stat().st_size, False)
            chunks.append(f"Path {target}\nsize={_human(size)}{' [truncated]' if truncated else ''}")
        else:
            try:
                proc = _run(["du", "-sh", str(target)], timeout=60)
                chunks.append(f"$ du -sh {target}\n{proc.stdout}{proc.stderr}")
            except Exception as exc:
                chunks.append(f"$ du -sh {target}\nERROR: {exc}")
    return _trim("\n".join(chunks))


@mcp.tool(
    title="Git status",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def git_status(path: str) -> str:
    """Show git status and current branch for a repository on the host."""
    repo = _git_repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    try:
        branch = _run(["git", "-C", str(repo), "branch", "--show-current"], timeout=15)
        status = _run(["git", "-C", str(repo), "status", "--short", "--branch"], timeout=15)
        out = f"$ git -C {repo} branch --show-current\n{branch.stdout}{branch.stderr}"
        out += f"\n$ git -C {repo} status --short --branch\n{status.stdout}{status.stderr}"
        return _trim(out)
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Git log",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def git_log(path: str, count: int = 10) -> str:
    """Show recent commits for a repository on the host."""
    repo = _git_repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    count = max(1, min(int(count), 100))
    try:
        proc = _run(
            ["git", "-C", str(repo), "log", f"-n{count}", "--pretty=format:%h %ad %an %s", "--date=short"],
            timeout=15,
        )
        if proc.returncode != 0:
            return f"ERROR: {proc.stderr.strip() or 'git log failed'}"
        return _trim(proc.stdout or "(no commits)")
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Git diff",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def git_diff(path: str, staged: bool = False) -> str:
    """Show uncommitted changes (or staged changes with staged=true) for a repository."""
    repo = _git_repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    try:
        stat_argv = ["git", "-C", str(repo), "diff", "--stat"] + (["--cached"] if staged else [])
        diff_argv = ["git", "-C", str(repo), "diff"] + (["--cached"] if staged else [])
        stat = _run(stat_argv, timeout=15)
        diff = _run(diff_argv, timeout=15)
        out = f"$ {' '.join(stat_argv)}\n{stat.stdout}{stat.stderr}"
        out += f"\n$ {' '.join(diff_argv)}\n{diff.stdout}{diff.stderr}"
        return _trim(out or "(no changes)")
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Git branches",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def git_branch(path: str) -> str:
    """List local and remote branches for a repository on the host."""
    repo = _git_repo(path)
    if isinstance(repo, dict):
        return f"ERROR: {repo['error']}"
    try:
        proc = _run(["git", "-C", str(repo), "branch", "-a", "-v"], timeout=15)
        if proc.returncode != 0:
            return f"ERROR: {proc.stderr.strip() or 'git branch failed'}"
        return _trim(proc.stdout or "(no branches)")
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool(
    title="Git commit",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    ),
)
def git_commit(path: str, message: str) -> dict[str, Any]:
    """Stage all changes and commit in a repository inside writable roots. Never pushes."""
    if (blocked := _gate("git_commit", "git")) is not None:
        return blocked
    repo = pathlib.Path(path).expanduser().resolve()
    if not _is_under(repo, WRITE_ROOTS):
        return {"ok": False, "error": f"Path is outside configured writable roots: {repo}"}
    checked = _git_repo(path)
    if isinstance(checked, dict):
        return checked
    message = message.strip()
    if not message:
        return {"ok": False, "error": "Commit message must not be empty."}
    try:
        status = _run(["git", "-C", str(repo), "status", "--porcelain"], timeout=15)
        if not status.stdout.strip():
            return {"ok": False, "error": "Nothing to commit; working tree is clean."}
        add = _run(["git", "-C", str(repo), "add", "-A"], timeout=30)
        if add.returncode != 0:
            return {"ok": False, "error": add.stderr.strip() or "git add failed"}
        commit = _run(["git", "-C", str(repo), "commit", "-m", message], timeout=30)
        if commit.returncode != 0:
            _policy.audit("git_commit", {"repo": str(repo), "error": "commit failed"}, False)
            return {"ok": False, "error": (commit.stdout + commit.stderr).strip() or "git commit failed"}
        _policy.audit("git_commit", {"repo": str(repo), "message": message[:200]}, True)
        return {"ok": True, "repo": str(repo), "output": _trim(commit.stdout.strip())}
    except Exception as exc:
        _policy.audit("git_commit", {"repo": str(repo), "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    title="Fetch URL",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def http_fetch(url: str, max_chars: int = 20000, timeout_seconds: int = 20) -> dict[str, Any]:
    """Fetch an http(s) URL from the host and return its text. Response size is capped."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid URL: {url!r}"}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "Only absolute http(s) URLs are allowed."}
    timeout = max(1, min(int(timeout_seconds), 60))
    byte_cap = min(MAX_DOWNLOAD, 5_000_000)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "claude-host-mcp/0.4"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(byte_cap + 1)
            truncated = len(raw) > byte_cap
            raw = raw[:byte_cap]
            charset = resp.headers.get_content_charset() or "utf-8"
            body = raw.decode(charset, errors="replace")
            limit = max(100, min(int(max_chars), 200000))
            return {
                "ok": True,
                "url": resp.geturl(),
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "truncated": truncated,
                "body": _trim(body, limit),
            }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(
    title="Check TCP port",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def network_check(host: str, port: int, timeout_seconds: int = 5) -> dict[str, Any]:
    """Test whether a TCP port on a host is reachable from this machine."""
    if not HOST_NAME.match(host):
        return {"ok": False, "error": f"Invalid host: {host!r}"}
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"Invalid port: {port!r}"}
    if not 1 <= port <= 65535:
        return {"ok": False, "error": f"Port out of range: {port}"}
    timeout = max(1, min(int(timeout_seconds), 30))
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"ok": True, "reachable": True, "host": host, "port": port, "latency_ms": elapsed_ms}
    except Exception as exc:
        return {"ok": False, "reachable": False, "host": host, "port": port, "error": f"{type(exc).__name__}: {exc}"}


@mcp.tool(
    title="Download file",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def download_file(url: str, dest: str, overwrite: bool = False, timeout_seconds: int = 60) -> dict[str, Any]:
    """Download an http(s) URL to a file inside writable roots. Total size is capped."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid URL: {url!r}"}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "Only absolute http(s) URLs are allowed."}
    target = pathlib.Path(dest).expanduser().resolve()
    if not _is_under(target, WRITE_ROOTS):
        return {"ok": False, "error": f"Destination is outside configured writable roots: {target}"}
    if target.exists() and not overwrite:
        return {"ok": False, "error": "Destination exists; set overwrite=true to replace it."}
    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "claude-host-mcp/0.4"})
        total = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(target, "wb") as fh:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    fh.close()
                    target.unlink(missing_ok=True)
                    return {"ok": False, "error": f"Download exceeds {MAX_DOWNLOAD} byte cap; aborted."}
                fh.write(chunk)
        return {"ok": True, "path": str(target), "bytes": total, "url": url}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


from . import files as _files
from . import gitx as _gitx
from . import jobs as _jobs
from . import mind as _mind
from . import ops as _ops
from . import policy as _policy_mod
from . import resources as _resources
from . import sessions as _sessions
from . import snapshots as _snapshots
from . import webdata as _webdata

_sessions.register(mcp)
_jobs.register(mcp)
_policy_mod.register(mcp)
_files.register(mcp)
_gitx.register(mcp)
_ops.register(mcp)
_snapshots.register(mcp)
_resources.register(mcp)
_mind.register(mcp)
_webdata.register(mcp)


def main() -> None:
    # Do not print to stdout in stdio transport; stdout carries MCP JSON-RPC.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
