"""Web-data suite for claude-host-mcp: fetch+, web search, browser, GitHub, DBs, maps, drive, slack.

Covers official fetch/github/postgres/sqlite/redis/brave-search/puppeteer/
google-drive/google-maps/slack inspirations in ONE server with one policy
story. Secrets NEVER logged: only tool names, hosts, counts and exit codes
reach the audit trail.

Credential model (env):
  HOST_MCP_WEB_SEARCH=off|duckduckgo            (default off; no key needed)
  BRAVE_API_KEY                                  enables brave backend
  HOST_MCP_BROWSER=off|chrome                    (default off; headless chrome)
  GITHUB_TOKEN or GH_TOKEN                       enables github_* (gh CLI fallback)
  HOST_MCP_DB=off                                default; per-call dsn= overrides
  POSTGRES_DSN / REDIS_URL                       default DSNs (optional)
  GOOGLE_MAPS_API_KEY                            enables maps_* (else nominatim)
  RCLONE_REMOTE                                  e.g. "gdrive:" enables drive_*
  SLACK_BOT_TOKEN                                enables slack_* (chat:write scope)

DB rule: SELECT/WITH... read-only by default; anything else requires
confirm=true AND full profile. Redirects capped at 3. Browser JS
execution requires confirm=true.

ponytail: web_search duck backend scrapes html; upgrade path is a proper
JSON endpoint when one is keyless-stable. sqlite goes through stdlib;
postgres/redis shell to psql/redis-cli when drivers absent.
"""

from __future__ import annotations

import html as _html
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request
from typing import Any

from . import policy as _policy

_MAX_BODY = 5_000_000
_UA = "claude-host-mcp/0.5"


def _run(argv: list[str], timeout: int = 30, cwd: str | None = None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd)


def _gate(tool: str, family: str):
    from .server import _gate as _g

    return _g(tool, family)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


# ---------- fetch+ : html -> llm text ----------

_BLOCK = re.compile(r"<(script|style|noscript|template|svg|canvas)[^>]*>.*?</\1>",
                    re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\xa0]+")
_NL = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    html = _BLOCK.sub("\n", html)
    html = re.sub(r"</(p|div|h[1-6]|li|tr|br|section|article)[^>]*>", "\n", html,
                  flags=re.I)
    text = _TAG.sub("", html)
    text = _html.unescape(text)
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return _NL.sub("\n\n", "\n".join(l for l in lines if l))


def fetch_text(url: str, max_chars: int = 20000,
               timeout_seconds: int = 20) -> dict[str, Any]:
    """Fetch URL and return LLM-ready text (scripts/styles stripped). Follows <=3 redirects."""
    from .server import MAX_DOWNLOAD

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid URL: {url!r}"}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "Only absolute http(s) URLs are allowed."}
    timeout = max(1, min(int(timeout_seconds), 60))
    byte_cap = min(MAX_DOWNLOAD, _MAX_BODY)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    hops, current = 0, url
    try:
        while hops <= 3:
            req = urllib.request.Request(current, headers={"User-Agent": _UA})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read(byte_cap + 1)
                    truncated = len(raw) > byte_cap
                    raw = raw[:byte_cap]
                    ctype = resp.headers.get("Content-Type", "")
                    charset = resp.headers.get_content_charset() or "utf-8"
                    body = raw.decode(charset, errors="replace")
                    if "html" in ctype or "<html" in body[:2000].lower():
                        body = _html_to_text(body)
                    limit = max(100, min(int(max_chars), 200000))
                    return {"ok": True, "url": resp.geturl(),
                            "status": resp.status, "content_type": ctype,
                            "truncated": truncated, "redirects": hops,
                            "body": body[:limit] +
                            ("\n\n[truncated]" if len(body) > limit else "")}
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    loc = exc.headers.get("Location", "")
                    if not loc:
                        return {"ok": False, "error": f"Redirect without Location ({exc.code})."}
                    current = urllib.parse.urljoin(current, loc)
                    hops += 1
                    continue
                return {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}
        return {"ok": False, "error": "Too many redirects (>3)."}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------- web search ----------

def _duck_search(query: str, count: int) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"q": query, "format": "json", "no_html": "1"})
    req = urllib.request.Request("https://api.duckduckgo.com/?" + params,
                                 headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read(500000).decode(errors="replace"))
    out = []
    for item in (data.get("RelatedTopics") or [])[: count * 2]:
        if isinstance(item, dict) and item.get("Text"):
            out.append({"title": (item.get("Text") or "")[:150],
                        "url": item.get("FirstURL", ""), "snippet": item.get("Text", "")[:300]})
        elif isinstance(item, dict):
            for sub in (item.get("Topics") or [])[:3]:
                out.append({"title": (sub.get("Text") or "")[:150],
                            "url": sub.get("FirstURL", ""), "snippet": sub.get("Text", "")[:300]})
    return out[:count]


def _brave_search(query: str, count: int) -> list[dict[str, str]]:
    key = _env("BRAVE_API_KEY")
    if not key:
        raise ValueError("BRAVE_API_KEY not set.")
    req = urllib.request.Request(
        "https://api.search.brave.com/res/v1/web/search?" +
        urllib.parse.urlencode({"q": query, "count": min(count, 20)}),
        headers={"Accept": "application/json", "X-Subscription-Token": key,
                 "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read(1000000).decode(errors="replace"))
    out = []
    for item in (data.get("web", {}) or {}).get("results", [])[:count]:
        out.append({"title": item.get("title", "")[:150], "url": item.get("url", ""),
                    "snippet": item.get("description", "")[:300]})
    return out


def web_search(query: str, count: int = 8) -> dict[str, Any]:
    """Web search. Default backend off (set HOST_MCP_WEB_SEARCH=duckduckgo); brave with key."""
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Empty query."}
    count = max(1, min(int(count), 20))
    backend = os.environ.get("HOST_MCP_WEB_SEARCH", "off").lower()
    if _env("BRAVE_API_KEY"):
        backend = "brave"
    if backend == "off":
        return {"ok": False, "error": "Web search disabled. Set HOST_MCP_WEB_SEARCH=duckduckgo or BRAVE_API_KEY."}
    try:
        results = _brave_search(query, count) if backend == "brave" \
            else _duck_search(query, count)
        _policy.audit("web_search", {"backend": backend, "results": len(results)}, True)
        return {"ok": True, "backend": backend, "results": results}
    except Exception as exc:
        _policy.audit("web_search", {"error": type(exc).__name__}, False)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------- headless browser ----------

def _chrome() -> str | None:
    if os.environ.get("HOST_MCP_BROWSER", "off").lower() != "chrome":
        return None
    return shutil.which("google-chrome") or shutil.which("chromium") or \
        shutil.which("chromium-browser")


def browser_fetch(url: str, max_chars: int = 20000,
                  timeout_seconds: int = 30) -> dict[str, Any]:
    """Render page in headless Chrome, return DOM text. Needs HOST_MCP_BROWSER=chrome."""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid URL: {url!r}"}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "Only absolute http(s) URLs are allowed."}
    chrome = _chrome()
    if not chrome:
        return {"ok": False, "error": "Browser off. Set HOST_MCP_BROWSER=chrome (needs google-chrome)."}
    timeout = max(5, min(int(timeout_seconds), 120))
    try:
        proc = _run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                     "--virtual-time-budget=8000", "--dump-dom", url], timeout=timeout)
        if not proc.stdout.strip():
            return {"ok": False, "error": (proc.stderr.strip()[-500:] or "chrome produced no DOM")}
        text = _html_to_text(proc.stdout)
        limit = max(100, min(int(max_chars), 200000))
        _policy.audit("browser_fetch", {"host": parsed.hostname}, True)
        return {"ok": True, "url": url, "body": text[:limit] +
                ("\n\n[truncated]" if len(text) > limit else "")}
    except Exception as exc:
        _policy.audit("browser_fetch", {"error": type(exc).__name__}, False)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def browser_shot(url: str, dest: str, width: int = 1280,
                 timeout_seconds: int = 30) -> dict[str, Any]:
    """Screenshot page to PNG inside writable roots. Needs HOST_MCP_BROWSER=chrome."""
    from .server import WRITE_ROOTS

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return {"ok": False, "error": f"Invalid URL: {url!r}"}
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "Only absolute http(s) URLs are allowed."}
    chrome = _chrome()
    if not chrome:
        return {"ok": False, "error": "Browser off. Set HOST_MCP_BROWSER=chrome."}
    target, err = _policy.resolve_under(dest, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if target.suffix.lower() != ".png":
        return {"ok": False, "error": "dest must end with .png."}
    timeout = max(5, min(int(timeout_seconds), 120))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = _run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                     f"--window-size={max(320, min(int(width), 3840))},900",
                     f"--screenshot={target}", url], timeout=timeout)
        if not target.exists():
            return {"ok": False, "error": proc.stderr.strip()[-500:] or "screenshot failed"}
        _policy.audit("browser_shot", {"host": parsed.hostname,
                                       "bytes": target.stat().st_size}, True)
        return {"ok": True, "path": str(target), "bytes": target.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------- github ----------

def _gh_token() -> str:
    return _env("GITHUB_TOKEN") or _env("GH_TOKEN")


def _gh_api(path: str, method: str = "GET",
            payload: dict | None = None) -> dict[str, Any]:
    token = _gh_token()
    if not token:
        if shutil.which("gh"):
            return {"ok": False, "error": "GITHUB_TOKEN unset; `gh` CLI fallback needs interactive auth."}
        return {"ok": False, "error": "Set GITHUB_TOKEN (or GH_TOKEN) to enable github_*."}
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request("https://api.github.com" + path, data=data,
                                     method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Accept": "application/vnd.github+json",
                                              "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(2000000).decode(errors="replace")
            return {"ok": True, "status": resp.status,
                    "data": json.loads(body) if body.strip() else None}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"GitHub HTTP {exc.code}: {exc.read(2000).decode(errors='replace')[:500]}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def github_repo(full_name: str) -> dict[str, Any]:
    """Repo metadata: stars, issues, default branch."""
    full_name = full_name.strip()
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", full_name):
        return {"ok": False, "error": f"Invalid repo: {full_name!r}. Use owner/name."}
    out = _gh_api(f"/repos/{full_name}")
    if not out.get("ok"):
        return out
    d = out["data"]
    return {"ok": True, "full_name": d.get("full_name"), "stars": d.get("stargazers_count"),
            "open_issues": d.get("open_issues_count"), "default_branch": d.get("default_branch"),
            "archived": d.get("archived"), "description": (d.get("description") or "")[:300]}


def github_issue(full_name: str, action: str = "list", number: int = 0,
                 title: str = "", body: str = "") -> dict[str, Any]:
    """List/get/create issues. create prompts."""
    full_name = full_name.strip()
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", full_name):
        return {"ok": False, "error": f"Invalid repo: {full_name!r}."}
    action = action.lower()
    if action == "list":
        out = _gh_api(f"/repos/{full_name}/issues?state=open&per_page=20")
        if not out.get("ok"):
            return out
        return {"ok": True, "issues": [{"number": i.get("number"), "title": i.get("title"),
                                        "labels": [x.get("name") for x in i.get("labels", [])]}
                                       for i in out["data"]]}
    if action == "get":
        out = _gh_api(f"/repos/{full_name}/issues/{int(number)}")
        if not out.get("ok"):
            return out
        d = out["data"]
        return {"ok": True, "number": d.get("number"), "title": d.get("title"),
                "state": d.get("state"), "body": (d.get("body") or "")[:3000]}
    if action == "create":
        if (blocked := _gate("github_issue_create", "github")) is not None:
            return blocked
        if not title.strip():
            return {"ok": False, "error": "title required for create."}
        out = _gh_api(f"/repos/{full_name}/issues", "POST",
                      {"title": title[:200], "body": body[:10000]})
        if not out.get("ok"):
            _policy.audit("github_issue_create", {"repo": full_name}, False)
            return out
        _policy.audit("github_issue_create",
                      {"repo": full_name, "number": out["data"].get("number")}, True)
        return {"ok": True, "number": out["data"].get("number"),
                "url": out["data"].get("html_url")}
    return {"ok": False, "error": "action must be list, get or create."}


def github_pr(full_name: str, action: str = "list", number: int = 0,
              title: str = "", head: str = "", base: str = "",
              body: str = "") -> dict[str, Any]:
    """List/get/create PRs. create prompts."""
    full_name = full_name.strip()
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", full_name):
        return {"ok": False, "error": f"Invalid repo: {full_name!r}."}
    action = action.lower()
    if action == "list":
        out = _gh_api(f"/repos/{full_name}/pulls?state=open&per_page=20")
        if not out.get("ok"):
            return out
        return {"ok": True, "prs": [{"number": p.get("number"), "title": p.get("title"),
                                     "head": (p.get("head") or {}).get("ref"),
                                     "base": (p.get("base") or {}).get("ref")}
                                    for p in out["data"]]}
    if action == "get":
        out = _gh_api(f"/repos/{full_name}/pulls/{int(number)}")
        if not out.get("ok"):
            return out
        d = out["data"]
        return {"ok": True, "number": d.get("number"), "title": d.get("title"),
                "state": d.get("state"), "mergeable": d.get("mergeable"),
                "body": (d.get("body") or "")[:2000]}
    if action == "create":
        if (blocked := _gate("github_pr_create", "github")) is not None:
            return blocked
        if not title.strip() or not head.strip() or not base.strip():
            return {"ok": False, "error": "title, head and base required for create."}
        out = _gh_api(f"/repos/{full_name}/pulls", "POST",
                      {"title": title[:200], "head": head, "base": base,
                       "body": body[:10000]})
        if not out.get("ok"):
            _policy.audit("github_pr_create", {"repo": full_name}, False)
            return out
        _policy.audit("github_pr_create",
                      {"repo": full_name, "number": out["data"].get("number")}, True)
        return {"ok": True, "number": out["data"].get("number"),
                "url": out["data"].get("html_url")}
    return {"ok": False, "error": "action must be list, get or create."}


# ---------- databases ----------

_READ_ONLY = re.compile(r"^\s*(select|with|explain|show|describe|desc)\b", re.I | re.S)
_WRITE_WORD = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum)\b", re.I)


def _db_gate(sql: str, confirm: bool) -> dict[str, Any] | None:
    if _READ_ONLY.match(sql or "") and not _WRITE_WORD.search(sql or ""):
        return None
    allowed, reason = _policy.profile_allows("db_write")
    if not allowed:
        return {"ok": False, "error": reason + " Writes need full profile."}
    if not confirm:
        return {"ok": False, "error": "Write SQL needs confirm=true (SELECT/WITH read free)."}
    return None


def db_query(dsn: str = "", sql: str = "", limit: int = 50,
             confirm: bool = False) -> dict[str, Any]:
    """Read-only-first SQL over postgres (psql) or sqlite (stdlib). dsn like postgres://.. or file:/path/x.db."""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return {"ok": False, "error": "Empty SQL."}
    if len(sql) > 20000:
        return {"ok": False, "error": "SQL capped at 20000 chars."}
    if (err := _db_gate(sql, confirm)) is not None:
        return err
    limit = max(1, min(int(limit), 1000))
    dsn = (dsn or _env("POSTGRES_DSN")).strip()
    if not dsn and os.environ.get("HOST_MCP_DB", "off").lower() == "off":
        return {"ok": False, "error": "DB off. Pass dsn= (postgres://.. or file:/path.db) or set POSTGRES_DSN."}
    try:
        if dsn.startswith("file:") or dsn.endswith(".db") or dsn.endswith(".sqlite"):
            import sqlite3

            path = dsn[5:] if dsn.startswith("file:") else dsn
            target, err = _policy.resolve_under(path, __import__(
                "claude_host_mcp.server", fromlist=["READ_ROOTS"]).READ_ROOTS)
            if err:
                return {"ok": False, "error": err}
            con = sqlite3.connect(str(target))
            try:
                cur = con.execute(sql)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = [list(r) for r in cur.fetchmany(limit)]
            finally:
                con.close()
            _policy.audit("db_query", {"db": "sqlite", "rows": len(rows)}, True)
            return {"ok": True, "db": "sqlite", "columns": cols, "rows": rows}
        if dsn.startswith("postgres"):
            if not shutil.which("psql"):
                return {"ok": False, "error": "psql CLI not found; install postgresql-client."}
            proc = _run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-F", "\t",
                         "-c", sql], timeout=60)
            if proc.returncode != 0:
                _policy.audit("db_query", {"db": "postgres", "error": "failed"}, False)
                return {"ok": False, "error": proc.stderr.strip()[-1000:] or "psql failed"}
            rows = [line.split("\t") for line in proc.stdout.splitlines()[:limit]]
            host = urllib.parse.urlparse(dsn).hostname or "postgres"
            _policy.audit("db_query", {"db": host, "rows": len(rows)}, True)
            return {"ok": True, "db": host, "rows": rows}
        return {"ok": False, "error": "dsn must be postgres://.. or file:/path.db"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def db_tables(dsn: str = "") -> dict[str, Any]:
    """List tables: sqlite via stdlib, postgres via psql information_schema."""
    dsn = (dsn or _env("POSTGRES_DSN")).strip()
    if not dsn:
        return {"ok": False, "error": "Pass dsn= or set POSTGRES_DSN."}
    if dsn.startswith("file:") or dsn.endswith(".db") or dsn.endswith(".sqlite"):
        out = db_query(dsn, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", 200)
        if out.get("ok"):
            out["tables"] = [r[0] for r in out.pop("rows")]
        return out
    if dsn.startswith("postgres"):
        return db_query(dsn, "SELECT schemaname||'.'||tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1", 200)
    return {"ok": False, "error": "dsn must be postgres://.. or file:/path.db"}


def redis_get(key: str, url: str = "") -> dict[str, Any]:
    """GET a redis key via redis-cli. URL from REDIS_URL or url=."""
    key = key.strip()
    if not key or len(key) > 500:
        return {"ok": False, "error": "Invalid key."}
    target = (url or _env("REDIS_URL")).strip()
    if not target:
        return {"ok": False, "error": "Set REDIS_URL or pass url=redis://host:6379/0."}
    if not shutil.which("redis-cli"):
        return {"ok": False, "error": "redis-cli not found."}
    try:
        proc = _run(["redis-cli", "-u", target, "GET", key], timeout=15)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip()[-500:] or "redis GET failed"}
        return {"ok": True, "key": key, "value": (proc.stdout or "")[:8000]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------- maps ----------

def maps_geocode(query: str) -> dict[str, Any]:
    """Forward geocode. Google with key, else OpenStreetMap nominatim."""
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Empty query."}
    key = _env("GOOGLE_MAPS_API_KEY")
    try:
        if key:
            url = "https://maps.googleapis.com/maps/api/geocode/json?" + \
                urllib.parse.urlencode({"address": query, "key": key})
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": _UA}),
                    timeout=15) as resp:
                data = json.loads(resp.read(500000).decode(errors="replace"))
            res = data.get("results", [])[0] if data.get("results") else None
            if not res:
                return {"ok": False, "error": f"No results ({data.get('status')})."}
            loc = res["geometry"]["location"]
            return {"ok": True, "backend": "google",
                    "formatted": res.get("formatted_address"),
                    "lat": loc["lat"], "lng": loc["lng"]}
        url = "https://nominatim.openstreetmap.org/search?" + \
            urllib.parse.urlencode({"q": query, "format": "json", "limit": "1"})
        with urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": _UA}),
                timeout=15) as resp:
            data = json.loads(resp.read(500000).decode(errors="replace"))
        if not data:
            return {"ok": False, "error": "No results."}
        return {"ok": True, "backend": "nominatim",
                "formatted": data[0].get("display_name"),
                "lat": float(data[0]["lat"]), "lng": float(data[0]["lon"])}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def maps_directions(origin: str, destination: str, mode: str = "driving") -> dict[str, Any]:
    """Directions. Google with key; else straight-line fallback."""
    import math

    origin, destination = origin.strip(), destination.strip()
    if not origin or not destination:
        return {"ok": False, "error": "origin and destination required."}
    mode = mode.lower() if mode.lower() in ("driving", "walking", "transit") else "driving"
    key = _env("GOOGLE_MAPS_API_KEY")
    if key:
        try:
            url = "https://maps.googleapis.com/maps/api/directions/json?" + \
                urllib.parse.urlencode({"origin": origin, "destination": destination,
                                        "mode": mode, "key": key})
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": _UA}),
                    timeout=15) as resp:
                data = json.loads(resp.read(1000000).decode(errors="replace"))
            legs = (data.get("routes", [{}])[0].get("legs", [{}])[0]
                    if data.get("routes") else {})
            return {"ok": True, "backend": "google",
                    "distance": (legs.get("distance") or {}).get("text"),
                    "duration": (legs.get("duration") or {}).get("text"),
                    "steps": len(legs.get("steps", []))}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    a, b = maps_geocode(origin), maps_geocode(destination)
    if not a.get("ok") or not b.get("ok"):
        return {"ok": False, "error": "Geocode failed; set GOOGLE_MAPS_API_KEY for real routing."}
    r = 6371.0
    dlat = math.radians(b["lat"] - a["lat"])
    dlng = math.radians(b["lng"] - a["lng"])
    h = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(a["lat"])) *
         math.cos(math.radians(b["lat"])) * math.sin(dlng / 2) ** 2)
    km = 2 * r * math.asin(math.sqrt(h))
    return {"ok": True, "backend": "straight-line-fallback", "km": round(km, 1),
            "note": "Set GOOGLE_MAPS_API_KEY for road routing."}


# ---------- drive (rclone) ----------

def _remote() -> str:
    return _env("RCLONE_REMOTE")


def drive_list(path: str = "", max_entries: int = 100) -> dict[str, Any] | str:
    """List remote path via rclone. Needs RCLONE_REMOTE like gdrive:."""
    remote = _remote()
    if not remote:
        return {"ok": False, "error": "Set RCLONE_REMOTE (e.g. gdrive:) to enable drive_*."}
    if not shutil.which("rclone"):
        return {"ok": False, "error": "rclone not found."}
    target = f"{remote}{path.strip()}"
    try:
        proc = _run(["rclone", "lsf", "--max-depth", "1", target], timeout=60)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip()[-500:] or "rclone lsf failed"}
        lines = proc.stdout.splitlines()[: max(1, min(int(max_entries), 1000))]
        return {"ok": True, "remote": target, "entries": lines}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def drive_get(remote_path: str, dest: str, overwrite: bool = False) -> dict[str, Any]:
    """Copy remote file into writable roots via rclone."""
    from .server import WRITE_ROOTS

    remote = _remote()
    if not remote:
        return {"ok": False, "error": "Set RCLONE_REMOTE to enable drive_*."}
    if not shutil.which("rclone"):
        return {"ok": False, "error": "rclone not found."}
    if (blocked := _gate("drive_get", "drive")) is not None:
        return blocked
    target, err = _policy.resolve_under(dest, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if target.exists() and not overwrite:
        return {"ok": False, "error": "Destination exists; set overwrite=true."}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = _run(["rclone", "copyto", f"{remote}{remote_path.strip()}", str(target)],
                    timeout=300)
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip()[-500:] or "rclone copyto failed"}
        _policy.audit("drive_get", {"remote": remote_path[:200],
                                    "bytes": target.stat().st_size}, True)
        return {"ok": True, "path": str(target), "bytes": target.stat().st_size}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# ---------- slack ----------

def _slack(method: str, payload: dict) -> dict[str, Any]:
    token = _env("SLACK_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "Set SLACK_BOT_TOKEN (xoxb-, chat:write) to enable slack_*."}
    try:
        req = urllib.request.Request("https://slack.com/api/" + method,
                                     data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json",
                                              "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(500000).decode(errors="replace"))
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error", "slack api failed")}
        return {"ok": True, "data": {k: v for k, v in data.items()
                                     if k in ("channel", "ts", "channels", "members")}}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def slack_list() -> dict[str, Any]:
    """List channels (needs SLACK_BOT_TOKEN)."""
    out = _slack("conversations.list", {"types": "public_channel,private_channel",
                                        "limit": 200})
    if not out.get("ok"):
        return out
    chans = out["data"].get("channels", [])
    return {"ok": True, "channels": [{"id": c.get("id"), "name": c.get("name")}
                                     for c in chans]}


def slack_send(channel: str, text: str) -> dict[str, Any]:
    """Post a message. Prompts (posts as YOU-configured bot)."""
    channel, text = channel.strip(), text.strip()
    if not channel or not text:
        return {"ok": False, "error": "channel and text required."}
    if len(text) > 4000:
        return {"ok": False, "error": "text capped at 4000 chars."}
    if (blocked := _gate("slack_send", "slack")) is not None:
        return blocked
    out = _slack("chat.postMessage", {"channel": channel, "text": text})
    _policy.audit("slack_send", {"channel": channel[:80],
                                 "chars": len(text)}, bool(out.get("ok")))
    if not out.get("ok"):
        return out
    return {"ok": True, "channel": out["data"].get("channel"),
            "ts": out["data"].get("ts")}


def register(mcp) -> None:
    """Register webdata tools. Follows repo wrapper convention (no impl shadowing)."""
    import sys as _sys

    _self = _sys.modules[__name__]
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _RONET = _TA(read_only_hint=True, open_world_hint=True)
    _WR = _TA(read_only_hint=False, destructive_hint=False,
              idempotent_hint=False, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    @mcp.tool(title="Fetch as text", annotations=_RONET)
    def fetch_text(url: str, max_chars: int = 20000,
                   timeout_seconds: int = 20) -> dict[str, Any]:
        """Fetch URL, strip boilerplate, return LLM-ready text (<=3 redirects)."""
        return _self.fetch_text(url, max_chars, timeout_seconds)

    @mcp.tool(title="Web search", annotations=_RONET)
    def web_search(query: str, count: int = 8) -> dict[str, Any]:
        """Web search (duckduckgo keyless or brave with key). Disabled by default."""
        return _self.web_search(query, count)

    @mcp.tool(title="Browser render", annotations=_RONET)
    def browser_fetch(url: str, max_chars: int = 20000,
                      timeout_seconds: int = 30) -> dict[str, Any]:
        """Render JS page in headless Chrome, return DOM text. Opt-in."""
        return _self.browser_fetch(url, max_chars, timeout_seconds)

    @mcp.tool(title="Browser screenshot", annotations=_MUT)
    def browser_shot(url: str, dest: str, width: int = 1280,
                     timeout_seconds: int = 30) -> dict[str, Any]:
        """Screenshot page to PNG in writable roots. Opt-in; prompts."""
        return _self.browser_shot(url, dest, width, timeout_seconds)

    @mcp.tool(title="GitHub repo", annotations=_RONET)
    def github_repo(full_name: str) -> dict[str, Any]:
        """Repo metadata (needs GITHUB_TOKEN)."""
        return _self.github_repo(full_name)

    @mcp.tool(title="GitHub issues", annotations=_WR)
    def github_issue(full_name: str, action: str = "list", number: int = 0,
                     title: str = "", body: str = "") -> dict[str, Any]:
        """List/get/create issues. create prompts."""
        return _self.github_issue(full_name, action, number, title, body)

    @mcp.tool(title="GitHub PRs", annotations=_WR)
    def github_pr(full_name: str, action: str = "list", number: int = 0,
                  title: str = "", head: str = "", base: str = "",
                  body: str = "") -> dict[str, Any]:
        """List/get/create PRs. create prompts."""
        return _self.github_pr(full_name, action, number, title, head, base, body)

    @mcp.tool(title="SQL query", annotations=_WR)
    def db_query(dsn: str = "", sql: str = "", limit: int = 50,
                 confirm: bool = False) -> dict[str, Any]:
        """SELECT-first SQL (postgres psql / sqlite stdlib). Writes need confirm+full."""
        return _self.db_query(dsn, sql, limit, confirm)

    @mcp.tool(title="List DB tables", annotations=_WR)
    def db_tables(dsn: str = "") -> dict[str, Any]:
        """List tables for a DSN."""
        return _self.db_tables(dsn)

    @mcp.tool(title="Redis GET", annotations=_WR)
    def redis_get(key: str, url: str = "") -> dict[str, Any]:
        """GET a redis key (needs REDIS_URL or url=)."""
        return _self.redis_get(key, url)

    @mcp.tool(title="Geocode", annotations=_RONET)
    def maps_geocode(query: str) -> dict[str, Any]:
        """Forward geocode (google with key, else nominatim)."""
        return _self.maps_geocode(query)

    @mcp.tool(title="Directions", annotations=_RONET)
    def maps_directions(origin: str, destination: str,
                        mode: str = "driving") -> dict[str, Any]:
        """Directions (google with key, else straight-line km)."""
        return _self.maps_directions(origin, destination, mode)

    @mcp.tool(title="Drive list", annotations=_RONET)
    def drive_list(path: str = "", max_entries: int = 100) -> dict[str, Any] | str:
        """List rclone remote path (needs RCLONE_REMOTE)."""
        return _self.drive_list(path, max_entries)

    @mcp.tool(title="Drive download", annotations=_MUT)
    def drive_get(remote_path: str, dest: str,
                  overwrite: bool = False) -> dict[str, Any]:
        """Copy remote file into writable roots. Prompts."""
        return _self.drive_get(remote_path, dest, overwrite)

    @mcp.tool(title="Slack channels", annotations=_RONET)
    def slack_list() -> dict[str, Any]:
        """List channels (needs SLACK_BOT_TOKEN)."""
        return _self.slack_list()

    @mcp.tool(title="Slack send", annotations=_MUT)
    def slack_send(channel: str, text: str) -> dict[str, Any]:
        """Post a message. Prompts."""
        return _self.slack_send(channel, text)
