"""Background jobs for claude-host-mcp.

Fire-and-forget commands with cursor-based incremental reads. Same
pattern as terminal sessions: start once, page output with job_output,
block with job_wait instead of polling in a loop.

ponytail: job history capped at 100 entries (oldest finished evicted).
Upgrade path: persist job records to sqlite under ~/.cache when agents
need cross-restart resumption.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import threading
import time
from typing import Any

_BUFFER_CAP = 2_000_000
_MAX_JOBS = 100
_SCHEMA = "job_"


class _Job:
    def __init__(self, jid: str, command: str, cwd: str, proc: subprocess.Popen):
        self.jid = jid
        self.command = command
        self.cwd = cwd
        self.proc = proc
        self.start_time = time.time()
        self.end_time: float | None = None
        self.lock = threading.Lock()
        self.stdout = ""
        self.stderr = ""
        self.stdout_truncated = False
        self.stderr_truncated = False
        self.exit_code: int | None = None

    def _append(self, attr: str, flag: str, text: str) -> None:
        with self.lock:
            if getattr(self, flag):
                return
            buf = getattr(self, attr)
            room = _BUFFER_CAP - len(buf)
            if room <= 0:
                setattr(self, flag, True)
                return
            setattr(self, attr, buf + text[:room])
            if len(text) > room:
                setattr(self, flag, True)

    def slice(self, stream: str, cursor: int, limit: int) -> tuple[str, int, int]:
        with self.lock:
            buf = self.stdout if stream == "stdout" else self.stderr
            total = len(buf)
            cursor = max(0, min(int(cursor), total))
            limit = max(1, min(int(limit), 100000))
            chunk = buf[cursor : cursor + limit]
            return chunk, cursor + len(chunk), total


_jobs: dict[str, _Job] = {}
_lock = threading.Lock()
_counter = 0
_IS_WINDOWS = os.name == "nt"


def _next_id() -> str:
    global _counter
    with _lock:
        _counter += 1
        return f"{_SCHEMA}{_counter}"


def _argv(command: str) -> list[str]:
    if _IS_WINDOWS:
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/bash", "-lc", command]


def _pump(stream, job: _Job, attr: str, flag: str) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            job._append(attr, flag, chunk.decode(errors="replace"))
    except Exception:
        pass


def start(command: str, cwd: str = "", timeout_seconds: int = 0) -> dict[str, Any]:
    """Launch command in background. timeout_seconds=0 means no watchdog kill."""
    if not command.strip():
        return {"ok": False, "error": "Empty command."}
    home = pathlib.Path.home().resolve()
    workdir = pathlib.Path(cwd).expanduser().resolve() if cwd else home
    if not workdir.is_dir():
        return {"ok": False, "error": f"Working directory does not exist: {workdir}"}
    timeout = max(0, min(int(timeout_seconds), 86400))
    try:
        proc = subprocess.Popen(
            _argv(command),
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            env=os.environ.copy(),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    jid = _next_id()
    job = _Job(jid, command, str(workdir), proc)
    with _lock:
        _jobs[jid] = job
        finished = [k for k, j in _jobs.items() if j.exit_code is not None]
        while len(_jobs) > _MAX_JOBS and finished:
            _jobs.pop(finished.pop(0), None)
    assert proc.stdout is not None and proc.stderr is not None
    threading.Thread(target=_pump, args=(proc.stdout, job, "stdout", "stdout_truncated"),
                     daemon=True).start()
    threading.Thread(target=_pump, args=(proc.stderr, job, "stderr", "stderr_truncated"),
                     daemon=True).start()
    threading.Thread(target=_reap, args=(job, timeout), daemon=True).start()
    return {"ok": True, "job_id": jid, "pid": proc.pid, "cwd": str(workdir)}


def _reap(job: _Job, timeout: int) -> None:
    try:
        job.exit_code = job.proc.wait(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        try:
            job.proc.kill()
            job.proc.wait(timeout=10)
        except Exception:
            pass
        job.exit_code = job.proc.poll()
        job._append("stderr", "stderr_truncated",
                    f"\n[job killed: exceeded timeout_seconds]\n")
    except Exception:
        pass
    finally:
        job.end_time = time.time()
        if job.exit_code is None:
            job.exit_code = job.proc.poll()


def _get(jid: str) -> _Job | None:
    with _lock:
        return _jobs.get(jid)


def status(jid: str) -> dict[str, Any]:
    j = _get(jid)
    if j is None:
        return {"ok": False, "error": f"Unknown job: {jid}"}
    running = j.proc.poll() is None
    return {"ok": True, "job_id": jid, "pid": j.proc.pid, "command": j.command,
            "cwd": j.cwd, "running": running, "exit_code": j.proc.poll(),
            "stdout_chars": len(j.stdout), "stderr_chars": len(j.stderr),
            "stdout_truncated": j.stdout_truncated, "stderr_truncated": j.stderr_truncated,
            "elapsed_seconds": int((j.end_time or time.time()) - j.start_time)}


def output(jid: str, stream: str = "stdout", cursor: int = 0,
           limit: int = 20000) -> dict[str, Any]:
    j = _get(jid)
    if j is None:
        return {"ok": False, "error": f"Unknown job: {jid}"}
    if stream not in ("stdout", "stderr"):
        return {"ok": False, "error": "stream must be stdout or stderr."}
    chunk, new_cursor, total = j.slice(stream, cursor, limit)
    running = j.proc.poll() is None
    return {"ok": True, "job_id": jid, "stream": stream, "output": chunk,
            "cursor": new_cursor, "total_chars": total,
            "running": running, "exit_code": j.proc.poll()}


def wait(jid: str, timeout_seconds: int = 60) -> dict[str, Any]:
    j = _get(jid)
    if j is None:
        return {"ok": False, "error": f"Unknown job: {jid}"}
    timeout = max(1, min(int(timeout_seconds), 600))
    try:
        code = j.proc.wait(timeout=timeout)
        return {"ok": True, "job_id": jid, "exited": True,
                "exit_code": code, "running": False}
    except subprocess.TimeoutExpired:
        return {"ok": True, "job_id": jid, "exited": False, "timeout": True,
                "exit_code": None, "running": True}


def cancel(jid: str) -> dict[str, Any]:
    j = _get(jid)
    if j is None:
        return {"ok": False, "error": f"Unknown job: {jid}"}
    if j.proc.poll() is not None:
        return {"ok": True, "job_id": jid, "already_exited": True,
                "exit_code": j.proc.poll()}
    try:
        j.proc.terminate()
        try:
            j.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            j.proc.kill()
            j.proc.wait(timeout=5)
        return {"ok": True, "job_id": jid, "cancelled": True,
                "exit_code": j.proc.poll()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_jobs(running_only: bool = False) -> list[dict[str, Any]]:
    with _lock:
        items = list(_jobs.items())
    out = []
    for jid, j in items:
        running = j.proc.poll() is None
        if running_only and not running:
            continue
        out.append({"job_id": jid, "pid": j.proc.pid, "command": j.command[:200],
                    "running": running, "exit_code": j.proc.poll(),
                    "elapsed_seconds": int((j.end_time or time.time()) - j.start_time)})
    return out


def register(mcp) -> None:
    """Register job_* tools on the given MCPServer."""
    from mcp.types import ToolAnnotations as _TA

    @mcp.tool(title="Start background job",
              annotations=_TA(read_only_hint=False, destructive_hint=False,
                              idempotent_hint=False, open_world_hint=False))
    def job_start(command: str, cwd: str = "", timeout_seconds: int = 0) -> dict[str, Any]:
        """Launch a command in background. Returns job_id; page with job_output, block with job_wait."""
        return start(command, cwd, timeout_seconds)

    @mcp.tool(title="Job status",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def job_status(job_id: str) -> dict[str, Any]:
        """Show state, pid, exit code and buffer sizes for a job."""
        return status(job_id)

    @mcp.tool(title="Job output",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def job_output(job_id: str, stream: str = "stdout", cursor: int = 0,
                   limit: int = 20000) -> dict[str, Any]:
        """Read new job output since cursor. Returns new cursor."""
        return output(job_id, stream, cursor, limit)

    @mcp.tool(title="Wait for job",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def job_wait(job_id: str, timeout_seconds: int = 60) -> dict[str, Any]:
        """Block until a job exits or timeout. Prefer over polling job_status."""
        return wait(job_id, timeout_seconds)

    @mcp.tool(title="Cancel job",
              annotations=_TA(read_only_hint=False, destructive_hint=True,
                              idempotent_hint=True, open_world_hint=False))
    def job_cancel(job_id: str) -> dict[str, Any]:
        """Terminate a running job (TERM, then KILL after 5s)."""
        return cancel(job_id)

    @mcp.tool(title="List jobs",
              annotations=_TA(read_only_hint=True, open_world_hint=False))
    def job_list(running_only: bool = False) -> list[dict[str, Any]]:
        """List background jobs, optionally only running ones."""
        return list_jobs(running_only)
