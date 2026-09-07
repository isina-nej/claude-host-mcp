"""Linux ops for claude-host-mcp: monitoring, journal, ports, docker, packages, net, diagnose.

Everything here is read-only except docker_*/package_*/service_restart,
which are destructive_hint=True (client prompts) and profile-gated.
Docker --privileged and package installs stay behind the developer/full
profiles; safe profile gets monitoring only.

Platform notes: monitoring/journal/ports use /proc + native binaries on
Linux, degrade gracefully on macOS (sysctl/vm_stat, launchctl, lsof or
netstat fallback), and use shutil/cross-platform calls on Windows.
Docker/package tools require their CLIs and report clean errors when
absent — never crash the server.

ponytail: system_snapshot samples /proc directly, no psutil dependency.
Upgrade path: add optional psutil backend for per-core history and
sensors when someone needs time-series instead of point samples.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any

from . import policy as _policy

_IS_WINDOWS = os.name == "nt"
_IS_MAC = __import__("platform").system() == "Darwin"
_SERVICE_NAME = re.compile(r"^[A-Za-z0-9@._:+-]{1,128}$")
_HOST_NAME = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


def _run(argv: list[str], timeout: int = 15):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _gate(tool: str, family: str):
    from .server import _gate as _g

    return _g(tool, family)


def _read_proc(path: str) -> str:
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return ""


# ---------- monitoring ----------

def _cpu() -> dict[str, Any]:
    if _IS_WINDOWS:
        return {"note": "per-core CPU needs psutil on Windows; use process_list instead"}
    try:
        with open("/proc/stat") as fh:
            parts = fh.readline().split()[1:]
        vals = [int(x) for x in parts[:8]]
        total, idle = sum(vals), vals[3] + (vals[4] if len(vals) > 4 else 0)
        time.sleep(0.3)
        with open("/proc/stat") as fh:
            parts2 = fh.readline().split()[1:]
        vals2 = [int(x) for x in parts2[:8]]
        total2, idle2 = sum(vals2), vals2[3] + (vals2[4] if len(vals2) > 4 else 0)
        pct = 100 * (1 - (idle2 - idle) / max(1, total2 - total))
        cores = sum(1 for line in open("/proc/cpuinfo") if line.startswith("processor"))
        model = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo")
                      if "model name" in l), "unknown")
        return {"usage_percent": round(pct, 1), "cores": cores, "model": model}
    except Exception:
        pass
    try:
        n = os.cpu_count() or 0
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else 0
        return {"usage_percent": None, "cores": n,
                "load_1m": load, "note": "coarse sample (no /proc)"}
    except Exception as exc:
        return {"error": str(exc)}


def _memory() -> dict[str, Any]:
    if _IS_WINDOWS:
        try:
            total, used, free = shutil.disk_usage(pathlib.Path.home().anchor)
            return {"note": "RAM detail needs psutil on Windows",
                    "disk_anchor_total": total, "disk_anchor_free": free}
        except Exception as exc:
            return {"error": str(exc)}
    if not _IS_MAC:
        try:
            info: dict[str, int] = {}
            for line in open("/proc/meminfo"):
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.strip().split()[0]) * 1024
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            swap_t = info.get("SwapTotal", 0)
            swap_f = info.get("SwapFree", 0)
            return {"total": total, "available": avail, "used": total - avail,
                    "used_percent": round(100 * (total - avail) / max(1, total), 1),
                    "swap_total": swap_t, "swap_used": swap_t - swap_f}
        except Exception:
            pass
    try:
        if _IS_MAC and shutil.which("vm_stat"):
            proc = _run(["vm_stat"], timeout=10)
            pages = {}
            for line in proc.stdout.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    pages[k.strip()] = int("".join(c for c in v if c.isdigit()) or 0)
            page = 16384
            free = (pages.get("Pages free", 0) + pages.get("Pages inactive", 0)) * page
            memsize = int(_run(["sysctl", "-n", "hw.memsize"], timeout=10).stdout.strip() or 0)
            return {"total": memsize, "free_approx": free,
                    "used": memsize - free if memsize else None}
    except Exception as exc:
        return {"error": str(exc)}
    return {"error": "memory info unavailable"}


def _disk() -> dict[str, Any]:
    try:
        total, used, free = shutil.disk_usage(str(pathlib.Path.home()))
        return {"path": str(pathlib.Path.home()), "total": total, "used": used,
                "free": free,
                "used_percent": round(100 * used / max(1, total), 1)}
    except Exception as exc:
        return {"error": str(exc)}


def _load() -> dict[str, Any]:
    try:
        if hasattr(os, "getloadavg"):
            a, b, c = os.getloadavg()
            return {"load_1m": a, "load_5m": b, "load_15m": c,
                    "cores": os.cpu_count()}
        return {"uptime": _read_proc("/proc/uptime").split()[0] if not _IS_WINDOWS else None}
    except Exception as exc:
        return {"error": str(exc)}


def _temps() -> dict[str, Any]:
    zones: dict[str, Any] = {}
    try:
        for zone in sorted(pathlib.Path("/sys/class/thermal").glob("thermal_zone*")):
            try:
                temp = int((zone / "temp").read_text().strip()) / 1000
                kind = (zone / "type").read_text().strip()
                zones[zone.name] = {"type": kind, "celsius": temp}
            except OSError:
                continue
    except OSError:
        pass
    if zones:
        return zones
    if _IS_MAC and shutil.which("powermetrics"):
        return {"note": "macOS temps need sudo powermetrics; skipped (requires root)"}
    return {"note": "no thermal zones found"}


def _battery() -> dict[str, Any]:
    try:
        for bat in sorted(pathlib.Path("/sys/class/power_supply").glob("BAT*")):
            cap = (bat / "capacity").read_text().strip()
            status = (bat / "status").read_text().strip()
            return {"battery": bat.name, "capacity_percent": int(cap), "status": status}
    except OSError:
        pass
    return {"note": "no battery found (desktop or VM?)"}


def _gpu() -> dict[str, Any]:
    if shutil.which("nvidia-smi"):
        try:
            proc = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                         "--format=csv,noheader,nounits"], timeout=15)
            if proc.returncode == 0 and proc.stdout.strip():
                gpus = []
                for line in proc.stdout.splitlines():
                    name, util, mu, mt = [x.strip() for x in line.split(",")]
                    gpus.append({"name": name, "util_percent": float(util),
                                 "mem_used_mib": float(mu), "mem_total_mib": float(mt)})
                return {"gpus": gpus}
        except Exception as exc:
            return {"error": str(exc)}
    return {"note": "nvidia-smi not found; no NVIDIA GPU visible"}


def _net_ifaces() -> dict[str, Any]:
    ifaces: dict[str, Any] = {}
    try:
        for iface in sorted(pathlib.Path("/sys/class/net").iterdir()):
            oper = _read_proc(str(iface / "operstate")) or "unknown"
            mac = _read_proc(str(iface / "address")) or "unknown"
            ifaces[iface.name] = {"state": oper, "mac": mac}
        if ifaces:
            return ifaces
    except OSError:
        pass
    try:
        info = socket.getaddrinfo(socket.gethostname(), None)
        return {"note": "no /sys/class/net; local addrs",
                "addrs": sorted({a[4][0] for a in info})}
    except Exception as exc:
        return {"error": str(exc)}


def system_snapshot() -> dict[str, Any]:
    """One call: cpu, memory, disk, load, temps, battery, gpu, network, uptime."""
    snap: dict[str, Any] = {"cpu": _cpu(), "memory": _memory(), "disk": _disk(),
                            "load": _load(), "temperatures": _temps(),
                            "battery": _battery(), "gpu": _gpu(),
                            "network_interfaces": _net_ifaces()}
    try:
        snap["uptime_seconds"] = float(_read_proc("/proc/uptime").split()[0])
    except Exception:
        snap["uptime_seconds"] = None
    return snap


# ---------- journal ----------

def journal(service: str = "", lines: int = 50, priority: str = "",
            since: str = "") -> str:
    """Query user journal. system journal needs root; use service_status for hints."""
    if service and not _SERVICE_NAME.match(service):
        return f"ERROR: invalid service name: {service!r}"
    prio = priority.strip().lower()
    if prio and prio not in ("debug", "info", "notice", "warning", "err",
                             "crit", "alert", "emerg"):
        return "ERROR: priority must be debug/info/notice/warning/err/crit/alert/emerg."
    n = max(1, min(int(lines), 500))
    if _IS_MAC:
        if not shutil.which("log"):
            return "ERROR: `log` is not available."
        argv = ["log", "show", "--last", since or "20m", "--style", "compact"]
        if service:
            argv += ["--predicate", f'process == "{service}"']
        try:
            proc = _run(argv, timeout=30)
            out = proc.stdout or proc.stderr
            return out[-20000:] or "(no log output)"
        except Exception as exc:
            return f"ERROR: {exc}"
    if not shutil.which("journalctl"):
        return "ERROR: journalctl is not available on this host."
    argv = ["journalctl", "--user", "--no-pager", "-n", str(n)]
    if service:
        argv += ["-u", service]
    if prio:
        argv += ["-p", prio]
    if since:
        argv += ["--since", since]
    try:
        proc = _run(argv, timeout=30)
        out = (proc.stdout or "") + (proc.stderr or "")
        return out[-20000:] or "(no journal output)"
    except Exception as exc:
        return f"ERROR: {exc}"


# ---------- ports ----------

def _port_table() -> list[dict[str, Any]]:
    """Listening sockets via /proc/net/tcp* (Linux). Fallback: ss/netstat/lsof."""
    rows: list[dict[str, Any]] = []
    if not _IS_WINDOWS and not _IS_MAC:
        try:
            inodes: dict[str, tuple[str, str]] = {}
            for path, ver in (("/proc/net/tcp", "4"), ("/proc/net/tcp6", "6")):
                try:
                    lines = open(path).readlines()[1:]
                except OSError:
                    continue
                for line in lines:
                    f = line.split()
                    if len(f) < 10 or f[3] != "0A":  # LISTEN only
                        continue
                    ip_hex, port_hex = f[1].rsplit(":", 1)
                    inodes[f[9]] = (ver, str(int(port_hex, 16)))
            # map inode -> pid/comm via /proc/*/fd
            for pid in filter(str.isdigit, os.listdir("/proc")):
                try:
                    fds = os.listdir(f"/proc/{pid}/fd")
                except OSError:
                    continue
                try:
                    with open(f"/proc/{pid}/comm") as fh:
                        comm = fh.read().strip()
                except OSError:
                    comm = "?"
                for fd in fds:
                    try:
                        link = os.readlink(f"/proc/{pid}/fd/{fd}")
                    except OSError:
                        continue
                    if link.startswith("socket:[") and link[8:-1] in inodes:
                        ver, port = inodes[link[8:-1]]
                        rows.append({"port": int(port), "ipv": ver,
                                     "pid": int(pid), "process": comm})
            if rows:
                seen = set()
                uniq = []
                for r in rows:
                    key = (r["port"], r["pid"])
                    if key not in seen:
                        seen.add(key)
                        uniq.append(r)
                return sorted(uniq, key=lambda r: r["port"])
        except Exception:
            pass
    for tool, argv in (("ss", ["ss", "-ltnp"]), ("netstat", ["netstat", "-ltnp"]),
                       ("lsof", ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])):
        if shutil.which(tool):
            try:
                proc = _run(argv, timeout=15)
                if proc.returncode == 0 and proc.stdout.strip():
                    return [{"raw": line} for line in proc.stdout.splitlines()[:100]]
            except Exception:
                continue
    return []


def port_list() -> list[dict[str, Any]] | str:
    rows = _port_table()
    if not rows:
        return "No listening-port data (no /proc, ss, netstat or lsof available)."
    return rows


def port_check(port: int, host: str = "127.0.0.1") -> dict[str, Any]:
    if not _HOST_NAME.match(host) and host not in ("127.0.0.1", "::1", "localhost"):
        return {"ok": False, "error": f"Invalid host: {host!r}"}
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"Invalid port: {port!r}"}
    if not 1 <= port <= 65535:
        return {"ok": False, "error": f"Port out of range: {port}"}
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=5):
            return {"ok": True, "reachable": True, "host": host, "port": port,
                    "latency_ms": int((time.monotonic() - start) * 1000)}
    except Exception as exc:
        return {"ok": False, "reachable": False, "host": host, "port": port,
                "error": f"{type(exc).__name__}: {exc}"}


def port_owner(port: int) -> dict[str, Any] | str:
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"Invalid port: {port!r}"}
    for row in _port_table():
        if isinstance(row, dict) and row.get("port") == port:
            out: dict[str, Any] = {"ok": True, **row}
            try:
                out["cmdline"] = open(f"/proc/{row['pid']}/cmdline", "rb").read(
                ).replace(b"\0", b" ").decode(errors="replace").strip()[:500]
                out["cwd"] = os.readlink(f"/proc/{row['pid']}/cwd")
            except OSError:
                pass
            return out
    raw = _port_table()
    if raw and isinstance(raw[0], dict) and "raw" in raw[0]:
        return {"ok": False, "error": f"No structured owner for port {port}. Raw table:",
                "raw": [r["raw"] for r in raw[:30]]}
    return {"ok": False, "error": f"No listener found on port {port}."}


# ---------- docker ----------

def _docker(*args: str, timeout: int = 30):
    if not shutil.which("docker"):
        return None
    return _run(["docker", *args], timeout=timeout)


def docker_ps(all: bool = False) -> str | list[dict[str, Any]]:
    proc = _docker("ps", "--format", "{{json .}}")
    if proc is None:
        return "ERROR: docker CLI not found."
    if proc.returncode != 0:
        return f"ERROR: {proc.stderr.strip() or 'docker ps failed'}"
    import json as _json

    rows = []
    for line in proc.stdout.splitlines():
        try:
            rows.append(_json.loads(line))
        except Exception:
            continue
    if not all:
        rows = [r for r in rows if "Up" in str(r.get("Status", ""))]
    return rows or "(no containers)"


def docker_logs(name: str, lines: int = 100) -> str:
    if not re.match(r"^[A-Za-z0-9_.-]{1,128}$", name):
        return f"ERROR: invalid container: {name!r}"
    proc = _docker("logs", "--tail", str(max(1, min(int(lines), 2000))), name,
                   timeout=30)
    if proc is None:
        return "ERROR: docker CLI not found."
    if proc.returncode != 0:
        return f"ERROR: {proc.stderr.strip() or 'docker logs failed'}"
    return (proc.stdout + proc.stderr)[-20000:] or "(no logs)"


def docker_inspect(name: str) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.-]{1,128}$", name):
        return {"ok": False, "error": f"Invalid container: {name!r}"}
    proc = _docker("inspect", name, timeout=15)
    if proc is None:
        return {"ok": False, "error": "docker CLI not found."}
    import json as _json

    try:
        data = _json.loads(proc.stdout or "[]")
        if not data:
            return {"ok": False, "error": f"No such container: {name}"}
        c = data[0]
        return {"ok": True, "name": name, "state": c.get("State"),
                "image": c.get("Config", {}).get("Image"),
                "ports": c.get("NetworkSettings", {}).get("Ports"),
                "mounts": c.get("Mounts")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _docker_mut(name: str, action: str) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.-]{1,128}$", name):
        return {"ok": False, "error": f"Invalid container: {name!r}"}
    if (blocked := _gate(f"docker_{action}", "docker")) is not None:
        return blocked
    if action in ("stop", "restart", "rm"):
        verb = {"stop": "docker_stop", "restart": "docker_restart", "rm": "docker_rm"}[action]
        allowed, reason = _policy.profile_allows(verb)
        if not allowed:
            return {"ok": False, "error": reason}
    argv = {"start": ["start", name], "stop": ["stop", "-t", "10", name],
            "restart": ["restart", "-t", "10", name], "rm": ["rm", name]}[action]
    proc = _docker(*argv, timeout=60)
    if proc is None:
        return {"ok": False, "error": "docker CLI not found."}
    if proc.returncode != 0:
        _policy.audit(f"docker_{action}", {"container": name,
                                           "error": proc.stderr.strip()[:300]}, False)
        return {"ok": False, "error": proc.stderr.strip() or f"docker {action} failed"}
    _policy.audit(f"docker_{action}", {"container": name}, True)
    return {"ok": True, "container": name, "action": action}


def docker_exec(name: str, command: str, timeout_seconds: int = 60) -> dict[str, Any]:
    if not re.match(r"^[A-Za-z0-9_.-]{1,128}$", name):
        return {"ok": False, "error": f"Invalid container: {name!r}"}
    if (blocked := _gate("docker_exec", "docker")) is not None:
        return blocked
    allowed, reason = _policy.profile_allows("docker_exec")
    if not allowed:
        return {"ok": False, "error": reason}
    if re.search(r"--privileged\b", command):
        return {"ok": False, "error": "docker --privileged is blocked by policy."}
    timeout = max(1, min(int(timeout_seconds), 180))
    proc = _docker("exec", name, "sh", "-c", command, timeout=timeout)
    if proc is None:
        return {"ok": False, "error": "docker CLI not found."}
    _policy.audit("docker_exec", {"container": name, "command": command[:200],
                                  "exit": proc.returncode}, proc.returncode == 0)
    return {"ok": proc.returncode == 0, "exit_code": proc.returncode,
            "stdout": proc.stdout[-20000:], "stderr": proc.stderr[-20000:]}


# ---------- packages ----------

def _pkg_manager() -> tuple[str, list[str], list[str], list[str]] | tuple[None, None, None, None]:
    """Detect native package manager. Returns (name, search, info, list-installed)."""
    candidates = [
        ("apt", ["apt", "search"], ["apt", "show"], ["apt", "list", "--installed"]),
        ("dnf", ["dnf", "search"], ["dnf", "info"], ["dnf", "list", "installed"]),
        ("pacman", ["pacman", "-Ss"], ["pacman", "-Si"], ["pacman", "-Q"]),
        ("zypper", ["zypper", "search"], ["zypper", "info"], ["zypper", "search", "-i"]),
        ("apk", ["apk", "search"], ["apk", "info"], ["apk", "info", "-v"]),
        ("brew", ["brew", "search"], ["brew", "info"], ["brew", "list"]),
        ("flatpak", ["flatpak", "search"], ["flatpak", "info"], ["flatpak", "list"]),
        ("snap", ["snap", "find"], ["snap", "info"], ["snap", "list"]),
    ]
    for name, search, info, installed in candidates:
        if shutil.which(name):
            return name, search, info, installed
    return None, None, None, None


def package_search(query: str, count: int = 20) -> str:
    name, search, _i, _l = _pkg_manager()
    if not name:
        return "ERROR: no supported package manager found."
    if not query.strip() or len(query) > 200:
        return "ERROR: invalid query."
    try:
        proc = _run([*search, query.strip()], timeout=60)
        out = (proc.stdout or proc.stderr or "").strip()
        lines = out.splitlines()[: max(1, min(int(count), 100))]
        return f"[{name}]\n" + ("\n".join(lines) or "(no results)")
    except Exception as exc:
        return f"ERROR: {exc}"


def package_info(name_q: str) -> str:
    mgr, _s, info, _l = _pkg_manager()
    if not mgr:
        return "ERROR: no supported package manager found."
    if not re.match(r"^[A-Za-z0-9_+.@:-]{1,200}$", name_q.strip()):
        return "ERROR: invalid package name."
    try:
        proc = _run([*info, name_q.strip()], timeout=30)
        out = (proc.stdout or proc.stderr or "").strip()
        return f"[{mgr}]\n" + (out[:8000] or "(no info)")
    except Exception as exc:
        return f"ERROR: {exc}"


def _pkg_mut(action: str, package: str) -> dict[str, Any]:
    mgr, *_ = _pkg_manager()
    if not mgr:
        return {"ok": False, "error": "no supported package manager found."}
    if not re.match(r"^[A-Za-z0-9_+.@:-]{1,200}$", package.strip()):
        return {"ok": False, "error": "Invalid package name."}
    if (blocked := _gate(f"package_{action}", "package")) is not None:
        return blocked
    allowed, reason = _policy.profile_allows(f"package_{action}")
    if not allowed:
        return {"ok": False, "error": reason}
    cmds = {
        "apt": {"install": ["apt-get", "install", "-y"], "remove": ["apt-get", "remove", "-y"],
                "update": ["apt-get", "update"]},
        "dnf": {"install": ["dnf", "install", "-y"], "remove": ["dnf", "remove", "-y"],
                "update": ["dnf", "check-update"]},
        "pacman": {"install": ["pacman", "-S", "--noconfirm"], "remove": ["pacman", "-R", "--noconfirm"],
                   "update": ["pacman", "-Sy"]},
        "brew": {"install": ["brew", "install"], "remove": ["brew", "uninstall"],
                 "update": ["brew", "update"]},
    }
    if mgr not in cmds:
        return {"ok": False, "error": f"Mutations unsupported for {mgr}; use search/info only."}
    argv = [*cmds[mgr][action], package.strip()] if action in ("install", "remove") \
        else cmds[mgr][action]
    try:
        proc = _run(argv, timeout=300)
        ok = proc.returncode == 0
        _policy.audit(f"package_{action}", {"manager": mgr, "package": package,
                                            "exit": proc.returncode}, ok)
        if not ok:
            return {"ok": False, "error": (proc.stdout + proc.stderr).strip()[-2000:]}
        return {"ok": True, "manager": mgr, "action": action, "package": package,
                "output": proc.stdout.strip()[-2000:]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------- network detail ----------

def dns_lookup(host: str) -> dict[str, Any]:
    if not _HOST_NAME.match(host):
        return {"ok": False, "error": f"Invalid host: {host!r}"}
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        return {"ok": True, "host": host, "addresses": addrs}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def interface_list() -> dict[str, Any]:
    return {"ok": True, "interfaces": _net_ifaces()}


def connection_list(limit: int = 50) -> str:
    n = max(1, min(int(limit), 200))
    for tool, argv in (("ss", ["ss", "-tunp"]), ("netstat", ["netstat", "-tunp"])):
        if shutil.which(tool):
            try:
                proc = _run(argv, timeout=15)
                if proc.returncode == 0:
                    return "\n".join(proc.stdout.splitlines()[: n + 1])
            except Exception as exc:
                return f"ERROR: {exc}"
    return "ERROR: neither ss nor netstat available."


# ---------- diagnose ----------

def diagnose(target: str) -> dict[str, Any]:
    """Layered diagnosis for host:port URLs or service:NAME.

    Runs DNS -> TCP -> owner/process -> HTTP -> resources, or
    systemd/launchd status -> process -> journal -> ports for services.
    Returns each layer with ok flags so the agent sees WHERE it broke.
    """
    target = target.strip()
    if not target:
        return {"ok": False, "error": "Empty target. Use host:port, http(s)://.. or service:NAME."}
    layers: list[dict[str, Any]] = []
    if target.startswith("service:"):
        name = target.split(":", 1)[1].strip()
        if not _SERVICE_NAME.match(name):
            return {"ok": False, "error": f"Invalid service: {name!r}"}
        from .server import service_status as _svc

        layers.append({"layer": "service", "output": str(_svc(name))[:2000]})
        layers.append({"layer": "process", "output": "see process_list filter=" + name})
        layers.append({"layer": "journal", "output": journal(name, lines=30)[:2000]})
        layers.append({"layer": "ports", "output": str(port_list())[:2000]})
        return {"ok": True, "target": target, "layers": layers}
    url = target if "://" in target else f"http://{target}"
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid target: {target!r}"}
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host:
        dns = dns_lookup(host)
        layers.append({"layer": "dns", **dns})
        tcp = port_check(port, host if host not in ("localhost",) else "127.0.0.1")
        layers.append({"layer": "tcp", **tcp})
        if port:
            owner = port_owner(port)
            layers.append({"layer": "port_owner",
                           **(owner if isinstance(owner, dict) else {"output": owner})})
    if parsed.scheme in ("http", "https"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "claude-host-mcp/diagnose"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read(2000).decode(errors="replace")
                layers.append({"layer": "http", "ok": True, "status": resp.status,
                               "body_head": body[:500]})
        except Exception as exc:
            layers.append({"layer": "http", "ok": False,
                           "error": f"{type(exc).__name__}: {exc}"})
    layers.append({"layer": "resources",
                   "load": _load(), "disk": _disk()})
    failed = [layer["layer"] for layer in layers if layer.get("ok") is False]
    return {"ok": not failed, "target": target, "failed_layers": failed, "layers": layers}


def register(mcp) -> None:
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _RONET = _TA(read_only_hint=True, open_world_hint=True)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    import sys as _sys
    _self = _sys.modules[__name__]  # impls; wrappers below share their names

    @mcp.tool(title="System snapshot", annotations=_RO)
    def system_snapshot() -> dict[str, Any]:
        """Point-in-time cpu/memory/disk/load/temps/battery/gpu/network/uptime."""
        return _self.system_snapshot()

    @mcp.tool(title="Query journal", annotations=_RO)
    def journal_query(service: str = "", lines: int = 50, priority: str = "",
                      since: str = "") -> str:
        """User journal tail, optional service/priority/since filter."""
        return _self.journal(service, lines, priority, since)

    @mcp.tool(title="List ports", annotations=_RO)
    def port_list() -> list[dict[str, Any]] | str:
        """Listening sockets with owner pid/process (/proc on Linux, ss/lsof fallback)."""
        return _self.port_list()

    @mcp.tool(title="Check port", annotations=_RONET)
    def port_check(port: int, host: str = "127.0.0.1") -> dict[str, Any]:
        """TCP connect to host:port with latency."""
        return _self.port_check(port, host)

    @mcp.tool(title="Port owner", annotations=_RO)
    def port_owner(port: int) -> dict[str, Any] | str:
        """Which process owns a listening port (pid, comm, cmdline, cwd)."""
        return _self.port_owner(port)

    @mcp.tool(title="Docker containers", annotations=_RO)
    def docker_ps(all: bool = False) -> str | list[dict[str, Any]]:
        """List containers (running by default, all=true for all)."""
        return _self.docker_ps(all)

    @mcp.tool(title="Docker logs", annotations=_RO)
    def docker_logs(container: str, lines: int = 100) -> str:
        """Tail logs of a container."""
        return _self.docker_logs(container, lines)

    @mcp.tool(title="Docker inspect", annotations=_RO)
    def docker_inspect(container: str) -> dict[str, Any]:
        """State, image, ports and mounts of a container."""
        return _self.docker_inspect(container)

    @mcp.tool(title="Docker start", annotations=_MUT)
    def docker_start(container: str) -> dict[str, Any]:
        """Start a container."""
        return _docker_mut(container, "start")

    @mcp.tool(title="Docker stop", annotations=_MUT)
    def docker_stop(container: str) -> dict[str, Any]:
        """Stop a container (10s timeout)."""
        return _docker_mut(container, "stop")

    @mcp.tool(title="Docker restart", annotations=_MUT)
    def docker_restart(container: str) -> dict[str, Any]:
        """Restart a container (10s timeout)."""
        return _docker_mut(container, "restart")

    @mcp.tool(title="Docker remove", annotations=_MUT)
    def docker_rm(container: str) -> dict[str, Any]:
        """Remove a (stopped) container."""
        return _docker_mut(container, "rm")

    @mcp.tool(title="Docker exec", annotations=_MUT)
    def docker_exec(container: str, command: str,
                    timeout_seconds: int = 60) -> dict[str, Any]:
        """Run sh -c inside a container. --privileged blocked."""
        return _self.docker_exec(container, command, timeout_seconds)

    @mcp.tool(title="Package search", annotations=_RO)
    def package_search(query: str, count: int = 20) -> str:
        """Search native package manager (apt/dnf/pacman/brew/...)."""
        return _self.package_search(query, count)

    @mcp.tool(title="Package info", annotations=_RO)
    def package_info(package: str) -> str:
        """Show package metadata from native manager."""
        return _self.package_info(package)

    @mcp.tool(title="Package install", annotations=_MUT)
    def package_install(package: str) -> dict[str, Any]:
        """Install a package (apt/dnf/pacman/brew). Developer/full profile only."""
        return _pkg_mut("install", package)

    @mcp.tool(title="Package remove", annotations=_MUT)
    def package_remove(package: str) -> dict[str, Any]:
        """Remove a package. Developer/full profile only."""
        return _pkg_mut("remove", package)

    @mcp.tool(title="Package update index", annotations=_MUT)
    def package_update() -> dict[str, Any]:
        """Refresh package index (apt-get update / brew update / ...)."""
        mgr, *_ = _pkg_manager()
        if not mgr:
            return {"ok": False, "error": "no supported package manager found."}
        if (blocked := _gate("package_update", "package")) is not None:
            return blocked
        allowed, reason = _policy.profile_allows("package_update")
        if not allowed:
            return {"ok": False, "error": reason}
        cmds = {"apt": ["apt-get", "update"], "dnf": ["dnf", "check-update"],
                "pacman": ["pacman", "-Sy"], "brew": ["brew", "update"]}
        if mgr not in cmds:
            return {"ok": False, "error": f"Update unsupported for {mgr}."}
        try:
            proc = _run(cmds[mgr], timeout=300)
            ok = proc.returncode in (0, 100)  # dnf 100 = updates available
            _policy.audit("package_update", {"manager": mgr,
                                             "exit": proc.returncode}, ok)
            return {"ok": ok, "manager": mgr,
                    "output": (proc.stdout + proc.stderr).strip()[-2000:]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @mcp.tool(title="DNS lookup", annotations=_RONET)
    def dns_lookup(host: str) -> dict[str, Any]:
        """Resolve a hostname to addresses."""
        return _self.dns_lookup(host)

    @mcp.tool(title="List interfaces", annotations=_RO)
    def interface_list() -> dict[str, Any]:
        """Network interfaces with state and MAC."""
        return _self.interface_list()

    @mcp.tool(title="List connections", annotations=_RO)
    def connection_list(limit: int = 50) -> str:
        """Active TCP/UDP sockets via ss or netstat."""
        return _self.connection_list(limit)

    @mcp.tool(title="Diagnose target", annotations=_RONET)
    def diagnose(target: str) -> dict[str, Any]:
        """Layered diagnosis: host:port/http(s):// (DNS/TCP/owner/HTTP/resources) or service:NAME."""
        return _self.diagnose(target)


__all__ = ["register", "system_snapshot", "journal", "port_list", "port_check",
           "port_owner", "docker_ps", "docker_logs", "docker_inspect",
           "docker_exec", "package_search", "package_info", "dns_lookup",
           "interface_list", "connection_list", "diagnose"]
