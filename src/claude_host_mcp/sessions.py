"""Persistent terminal sessions for claude-host-mcp.

One shell process per session, kept alive across tool calls. Interactive
stdin stays open so agents can drive long-lived programs (dev servers,
REPLs, ssh) without polling run_command.

Unix shells: /bin/bash -s (reads commands from stdin, no prompt noise).
Windows: powershell -NoProfile -NoLogo -NonInteractive (stdin-driven).

Reader thread appends decoded output to an in-memory buffer. Consumers
page it with cursor offsets -> incremental output, low token cost.

ponytail: buffers are in-memory only (lost on server restart) and capped
at ~2M chars. Upgrade path: spill to rotating files under ~/.cache when
sessions exceed cap; resize is stored metadata only (no real PTY ioctl).
True PTY (pty.openpty + ioctl TIOCSWINSZ) is the upgrade for curses apps
like htop — pipes cover everything line-oriented today.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import threading
import time
from typing import Any

_BUFFER_CAP = 2_000_000
_SCHEMA = "term_"


class _Session:
    def __init__(self, sid: str, proc: subprocess.Popen, cwd: str, cols: int, rows: int):
        self.sid = sid
        self.proc = proc
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.buffer = ""
        self.truncated = False
        self.exit_code: int | None = None

    def append(self, text: str) -> None:
        with self.lock:
            if self.truncated:
                return
            room = _BUFFER_CAP - len(self.buffer)
            if room <= 0:
                self.truncated = True
                return
            self.buffer += text[:room]
            if len(text) > room:
                self.truncated = True

    def slice(self, cursor: int, limit: int) -> tuple[str, int, int]:
        with self.lock:
            total = len(self.buffer)
            cursor = max(0, min(int(cursor), total))
            limit = max(1, min(int(limit), 100000))
            return self.buffer[cursor : cursor + limit], cursor + len(
                self.buffer[cursor : cursor + limit]
            ), total


_sessions: dict[str, _Session] = {}
_lock = threading.Lock()
_counter = 0
_IS_WINDOWS = os.name == "nt"


def _next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"{_SCHEMA}{_counter}"


def _shell_argv() -> list[str]:
    if _IS_WINDOWS:
        return ["powershell", "-NoProfile", "-NoLogo", "-NonInteractive"]
    return ["/bin/bash", "--noprofile", "--norc", "-s"]


def _wrap_argv(command: str) -> list[str]:
    if _IS_WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-lc", command]


def _reader(session: _Session) -> None:
    try:
        assert session.proc.stdout is not None
        while True:
            chunk = session.proc.stdout.read(4096)
            if not chunk:
                break
            session.append(chunk.decode(errors="replace"))
    except Exception:
        pass
    finally:
        try:
            session.proc.wait(timeout=5)
        except Exception:
            pass
        session.exit_code = session.proc.poll()


def create(command: str = "", cwd: str = "", cols: int = 80, rows: int = 24) -> dict[str, Any]:
    home = pathlib.Path.home().resolve()
    workdir = pathlib.Path(cwd).expanduser().resolve() if cwd else home
    if not workdir.is_dir():
        return {"ok": False, "error": f"Working directory does not exist: {workdir}"}
    argv = _wrap_argv(command) if command.strip() else _shell_argv()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
            env=os.environ.copy(),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    sid = _next_id()
    session = _Session(sid, proc, str(workdir), max(20, min(cols, 500)), max(5, min(rows, 200)))
    with _lock:
        _sessions[sid] = session
    thread = threading.Thread(target=_reader, args=(session,), daemon=True)
    thread.start()
    return {"ok": True, "session_id": sid, "pid": proc.pid, "cwd": str(workdir)}


def _get(sid: str) -> _Session | None:
    with _lock:
        return _sessions.get(sid)


def read(sid: str, cursor: int = 0, limit: int = 20000) -> dict[str, Any]:
    s = _get(sid)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    out, new_cursor, total = s.slice(cursor, limit)
    alive = s.proc.poll() is None
    return {
        "ok": True,
        "session_id": sid,
        "output": out,
        "cursor": new_cursor,
        "total_chars": total,
        "truncated": s.truncated,
        "alive": alive,
        "exit_code": s.proc.poll(),
    }


def write(sid: str, data: str) -> dict[str, Any]:
    s = _get(sid)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    if s.proc.poll() is not None:
        return {"ok": False, "error": f"Session exited (code {s.proc.poll()})."}
    if not data:
        return {"ok": False, "error": "Empty write."}
    if len(data) > 100000:
        return {"ok": False, "error": "Write capped at 100000 chars per call."}
    try:
        assert s.proc.stdin is not None
        s.proc.stdin.write(data.encode())
        s.proc.stdin.flush()
        return {"ok": True, "session_id": sid, "bytes": len(data.encode())}
    except BrokenPipeError:
        return {"ok": False, "error": "Session stdin is closed."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def resize(sid: str, cols: int, rows: int) -> dict[str, Any]:
    s = _get(sid)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    s.cols = max(20, min(int(cols), 500))
    s.rows = max(5, min(int(rows), 200))
    # ponytail: metadata only; real PTY ioctl when we move to pty.openpty.
    return {"ok": True, "session_id": sid, "cols": s.cols, "rows": s.rows}


def signal(sid: str, signal_name: str = "INT") -> dict[str, Any]:
    import signal as _sig

    names = {"INT": _sig.SIGINT, "TERM": _sig.SIGTERM, "KILL": _sig.SIGKILL}
    if hasattr(_sig, "SIGHUP"):
        names["HUP"] = _sig.SIGHUP
    key = signal_name.upper()
    if key not in names:
        return {"ok": False, "error": f"Unknown signal {signal_name!r}; use one of {sorted(names)}."}
    s = _get(sid)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    if s.proc.poll() is not None:
        return {"ok": False, "error": "Session already exited."}
    try:
        s.proc.send_signal(names[key])
        return {"ok": True, "session_id": sid, "signal": key}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def wait(
    sid: str,
    pattern: str = "",
    wait_exit: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    s = _get(sid)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    timeout = max(1, min(int(timeout_seconds), 300))
    regex = None
    if pattern:
        try:
            regex = re.compile(pattern, re.S)
        except re.error as exc:
            return {"ok": False, "error": f"Invalid regex: {exc}"}
    deadline = time.monotonic() + timeout
    start_len = len(s.buffer)
    while True:
        with s.lock:
            haystack = s.buffer[start_len:]
            exited = s.proc.poll()
        if regex and regex.search(haystack):
            out, new_cursor, total = s.slice(start_len, 100000)
            return {"ok": True, "matched": True, "session_id": sid,
                    "output": out, "exit_code": exited, "alive": exited is None}
        if wait_exit and exited is not None:
            out, _, _ = s.slice(start_len, 100000)
            return {"ok": True, "matched": False, "exited": True,
                    "session_id": sid, "output": out, "exit_code": exited, "alive": False}
        if time.monotonic() >= deadline:
            out, _, _ = s.slice(start_len, 100000)
            return {"ok": True, "matched": False, "timeout": True,
                    "session_id": sid, "output": out, "exit_code": exited,
                    "alive": exited is None}
        time.sleep(0.2)


def close(sid: str) -> dict[str, Any]:
    with _lock:
        s = _sessions.pop(sid, None)
    if s is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    try:
        if s.proc.poll() is None:
            s.proc.terminate()
            try:
                s.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                s.proc.kill()
        return {"ok": True, "session_id": sid, "exit_code": s.proc.poll()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_sessions() -> list[dict[str, Any]]:
    with _lock:
        items = list(_sessions.items())
    out = []
    for sid, s in items:
        alive = s.proc.poll() is None
        out.append({"session_id": sid, "pid": s.proc.pid, "cwd": s.cwd,
                    "alive": alive, "exit_code": s.proc.poll(),
                    "buffer_chars": len(s.buffer),
                    "age_seconds": int(time.time() - s.start_time)})
    return out


def register(mcp) -> None:
    """Register terminal_* tools on the given MCPServer."""
    from mcp.types import ToolAnnotations as _TA

    @mcp.tool(title="Create terminal session",
              annotations=_TA(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=False, open_world_hint=False))
    def terminal_create(command: str = "", cwd: str = "", cols: int = 80,
                        rows: int = 24) -> dict[str, Any]:
        """Start a persistent shell session (or run a command interactively).

        Empty command spawns a login shell kept alive across calls. Use
        terminal_write to send input, terminal_read for incremental output,
        terminal_wait to block for a pattern/exit instead of polling.
        """
        return create(command, cwd, cols, rows)

    @mcp.tool(title="Read terminal output",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def terminal_read(session_id: str, cursor: int = 0,
                      limit: int = 20000) -> dict[str, Any]:
        """Read new output from a terminal session since cursor. Returns new cursor."""
        return read(session_id, cursor, limit)

    @mcp.tool(title="Write to terminal",
              annotations=_TA(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=False, open_world_hint=False))
    def terminal_write(session_id: str, data: str) -> dict[str, Any]:
        """Send keystrokes/commands to a terminal session's stdin."""
        return write(session_id, data)

    @mcp.tool(title="Resize terminal",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def terminal_resize(session_id: str, cols: int = 80, rows: int = 24) -> dict[str, Any]:
        """Store terminal dimensions (metadata; no PTY ioctl yet)."""
        return resize(session_id, cols, rows)

    @mcp.tool(title="Signal terminal",
              annotations=_TA(read_only_hint=False, destructive_hint=True,
                              idempotent_hint=True, open_world_hint=False))
    def terminal_signal(session_id: str, signal_name: str = "INT") -> dict[str, Any]:
        """Send INT/TERM/KILL (HUP on Unix) to a terminal session."""
        return signal(session_id, signal_name)

    @mcp.tool(title="Wait on terminal",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def terminal_wait(session_id: str, pattern: str = "", wait_exit: bool = False,
                      timeout_seconds: int = 30) -> dict[str, Any]:
        """Block until regex pattern appears in new output, process exits, or timeout."""
        return wait(session_id, pattern, wait_exit, timeout_seconds)

    @mcp.tool(title="Close terminal",
              annotations=_TA(read_only_hint=False, destructive_hint=True,
                              idempotent_hint=True, open_world_hint=False))
    def terminal_close(session_id: str) -> dict[str, Any]:
        """Terminate a terminal session and release it."""
        return close(session_id)

    @mcp.tool(title="List terminals",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def terminal_list() -> list[dict[str, Any]]:
        """List live terminal sessions with pid, cwd, age and buffer size."""
        return list_sessions()
