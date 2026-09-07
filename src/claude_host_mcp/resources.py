"""MCP resources for claude-host-mcp: live context without tool calls.

Static resources (always fresh on read):
  system://summary      compact identity/uptime/disk/memory line
  system://snapshot     full system_snapshot JSON
  system://ports        listening-port table JSON
  policy://current      active profile, roots, caps, destructive set
  audit://recent        last 20 audit records JSON

Template resources:
  process://{pid}       one process: ps row + cmdline + cwd (Linux /proc)
  terminal://{session}  terminal buffer tail + alive state
  job://{job_id}        job status + stdout/stderr tails

Git status stays a tool (git_status): SDK template matching on this
version does not resolve {?query} expansion, so git://status{?path}
reads fail with Unknown resource. All paths re-checked against
READ_ROOTS on every read — resources never bypass roots.
"""

from __future__ import annotations

import json
import os
import urllib.parse


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1, default=str)


def register(mcp) -> None:
    @mcp.resource("system://summary", name="system-summary",
                  title="Host summary", description="One-line identity, uptime, disk, memory.",
                  mime_type="text/plain")
    def _system_summary() -> str:
        from .server import host_identity, disk_usage

        ident = host_identity()
        return (f"{ident['hostname']} ({ident['os']} {ident['release']}) "
                f"user={ident['user']} pid={ident['pid']}\n{disk_usage()}")

    @mcp.resource("system://snapshot", name="system-snapshot",
                  title="System snapshot", description="Full cpu/memory/disk/load/gpu/net JSON.",
                  mime_type="application/json")
    def _system_snapshot() -> str:
        from .ops import system_snapshot

        return _json(system_snapshot())

    @mcp.resource("system://ports", name="system-ports",
                  title="Listening ports", description="Listening sockets with owners JSON.",
                  mime_type="application/json")
    def _system_ports() -> str:
        from .ops import port_list

        return _json(port_list())

    @mcp.resource("policy://current", name="policy-current",
                  title="Active policy", description="Profile, roots, caps, destructive tools JSON.",
                  mime_type="application/json")
    def _policy_current() -> str:
        from . import policy as _policy
        from .server import READ_ROOTS, WRITE_ROOTS, MAX_OUTPUT, MAX_TIMEOUT, MAX_DOWNLOAD

        return _json({"profile": _policy.PROFILE,
                      "read_roots": [str(p) for p in READ_ROOTS],
                      "write_roots": [str(p) for p in WRITE_ROOTS],
                      "max_output": MAX_OUTPUT, "max_timeout": MAX_TIMEOUT,
                      "max_download": MAX_DOWNLOAD,
                      "destructive_tools": sorted(_policy.DESTRUCTIVE)})

    @mcp.resource("audit://recent", name="audit-recent",
                  title="Recent audit", description="Last 20 audit records JSON.",
                  mime_type="application/json")
    def _audit_recent() -> str:
        from .policy import audit_tail

        return _json(audit_tail(20))

    @mcp.resource("process://{pid}", name="process-detail",
                  title="Process detail", description="One process: ps row, cmdline, cwd.",
                  mime_type="application/json")
    def _process_detail(pid: str) -> str:
        from .server import process_list

        try:
            number = int(pid)
        except ValueError:
            return _json({"ok": False, "error": f"Invalid pid: {pid!r}"})
        rows = str(process_list(filter=str(number), limit=10)).splitlines()
        detail: dict = {"ok": True, "pid": number, "ps": rows}
        try:
            detail["cmdline"] = open(f"/proc/{number}/cmdline", "rb").read(
            ).replace(b"\0", b" ").decode(errors="replace").strip()[:500]
            detail["cwd"] = os.readlink(f"/proc/{number}/cwd")
        except OSError as exc:
            detail["proc_note"] = str(exc)
        return _json(detail)

    @mcp.resource("terminal://{session}", name="terminal-buffer",
                  title="Terminal buffer", description="Terminal tail + alive state.",
                  mime_type="application/json")
    def _terminal_buffer(session: str) -> str:
        from .sessions import read

        sid = urllib.parse.unquote(session)
        result = read(sid, cursor=max(0, 0), limit=8000)
        if not result.get("ok"):
            return _json(result)
        # tail: last 8000 chars regardless of cursor paging
        total = result.get("total_chars", 0)
        tail = read(sid, cursor=max(0, total - 8000), limit=8000)
        return _json({"session_id": sid, "alive": tail.get("alive"),
                      "exit_code": tail.get("exit_code"),
                      "total_chars": total, "tail": tail.get("output", "")})

    @mcp.resource("job://{job_id}", name="job-detail",
                  title="Job detail", description="Job status + stdout/stderr tails.",
                  mime_type="application/json")
    def _job_detail(job_id: str) -> str:
        from .jobs import status, output

        jid = urllib.parse.unquote(job_id)
        st = status(jid)
        if not st.get("ok"):
            return _json(st)
        out = output(jid, "stdout", cursor=max(0, st.get("stdout_chars", 0) - 4000),
                     limit=4000)
        err = output(jid, "stderr", cursor=max(0, st.get("stderr_chars", 0) - 4000),
                     limit=4000)
        return _json({"status": st, "stdout_tail": out.get("output", ""),
                      "stderr_tail": err.get("output", "")})

    # ponytail: SDK matches simple {var} path templates but not {?query}
    # expansion on this version, so git status stays a TOOL (git_status).
    # Revisit when MCP SDK template matching supports query variables.
