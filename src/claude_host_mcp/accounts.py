"""Accounts suite for claude-host-mcp: GitHub, Vercel, Cloudflare as managed tools.

Two problems solved here, not one:

1. TOKEN STORE. Every tool resolves credentials in the same order:
   explicit env  >  stored file (chmod 600, set via accounts_complete)
   >  CLI auto-login (vercel auth.json, `gh auth token`)  >  keyless
   (GitHub public reads only). Secrets never reach the audit log — only
   provider names, hosts, counts and exit codes.

2. LINK + WAIT FLOW. MCP cannot open a browser, and agents should not
   paste secrets into chat blindly. So connecting is a 3-step dance:
     accounts_connect(provider) -> {action:"open-link", url, instructions,
        request_id} plus a live `login` sub-flow when the provider
        supports OAuth device flow or a localhost callback
        (or {already:true})
     ...user opens the link in THEIR browser, authorizes, copies token/code...
     accounts_complete(request_id, token) -> validates against the real
        API, stores chmod 600, marks request done
     accounts_wait(request_id, timeout) -> blocks until the request is
        done (user completed elsewhere) or already-authed CLI state
        appears. Polls every 5s. This is the "give link and wait" primitive.

Providers and their live login sub-flows:
  github     device flow (no user-made OAuth app needed): accounts_connect
             may return login.user_code + login.verification_uri when
             HOST_MCP_GITHUB_CLIENT_ID is set or reachable; else the classic
             PAT page + `gh auth login --web` CLI fallback. Reads keyless
             (60/hr) or gh CLI; token raises quota + create.
             link: https://github.com/settings/tokens/new (scopes listed).
             env: HOST_MCP_GITHUB_CLIENT_ID (optional; default is the public
             GitHub CLI client id used with device flow).
  vercel     accounts_connect tries a localhost callback server when the
             user supplies HOST_MCP_VERCEL_CLIENT_ID (their own Integration
             client id); otherwise link/CLI flow. auto-uses `vercel` CLI
             login when present; else VERCEL_TOKEN.
             link: https://vercel.com/account/tokens. REST v9/v13/v6/v2.
             env: HOST_MCP_VERCEL_CLIENT_ID, HOST_MCP_VERCEL_REDIRECT_PORT.
  cloudflare CF_API_TOKEN/CLOUDFLARE_API_KEY (+ optional account id).
             link: https://dash.cloudflare.com/profile/api-tokens with the
             Zone:Read + DNS:Edit template. No CLI exists; token-only.

Vercel redeploy, Cloudflare DNS create/delete + purge, and account_remove
are destructive (client prompts). Everything else reads free.

ponytail: vercel redeploy uses REST POST /v13/deployments {deploymentId,
name}; if Vercel reshapes it, fall back to `vercel redeploy <url> --yes`
via jobs. Cloudflare needs token scopes Zone:Read minimum; 403s pass
through verbatim so the user sees which scope is missing.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from . import policy as _policy

_UA = "claude-host-mcp/0.8"
_ACCT_DIR = pathlib.Path.home() / ".local/share/claude-host-mcp/accounts"
_REQ_DIR = _ACCT_DIR / "requests"
_PROVIDERS = ("github", "vercel", "cloudflare")

# GitHub device flow uses the public GitHub CLI OAuth app id by default.
# No secret is involved (device flow is public-client); the user may
# override with their own OAuth app via HOST_MCP_GITHUB_CLIENT_ID.
_GH_DEVICE_CLIENT_ID = "178c6fc778ccc68e1d6"
_GH_DEVICE_SCOPE = "repo workflow gist read:user"
_VERCEL_OAUTH_HOST = "https://vercel.com"
_VERCEL_OAUTH_PATH = "/oauth/authorize"
_VERCEL_TOKEN_PATH = "/v2/oauth/access_token"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _run(argv: list[str], timeout: int = 20):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _gate(tool: str, family: str = "accounts"):
    from .server import _gate as _g

    return _g(tool, family)


def _store(provider: str) -> pathlib.Path:
    _ACCT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_ACCT_DIR, 0o700)
    except OSError:
        pass
    return _ACCT_DIR / f"{provider}.json"


def _read_stored(provider: str) -> str:
    try:
        data = json.loads((_ACCT_DIR / f"{provider}.json").read_text())
        token = str(data.get("token") or "").strip()
        return token
    except (OSError, ValueError):
        return ""


def _write_stored(provider: str, token: str, label: str = "") -> None:
    path = _store(provider)
    path.write_text(json.dumps({"token": token, "label": label[:120],
                                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                          time.localtime())}))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------- per-provider credential resolution ----------

def _vercel_cli_token() -> str:
    try:
        data = json.loads((pathlib.Path.home() /
                           ".local/share/com.vercel.cli/auth.json").read_text())
        return str(data.get("token") or "").strip()
    except (OSError, ValueError):
        return ""


def _gh_cli_token() -> str:
    if not shutil.which("gh"):
        return ""
    try:
        proc = _run(["gh", "auth", "token"], timeout=10)
        if proc.returncode == 0 and proc.stdout.strip().startswith(("gho_", "ghp_")):
            return proc.stdout.strip()
    except Exception:
        pass
    return ""


def _gh_cli_authed() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return _run(["gh", "auth", "status"], timeout=10).returncode == 0
    except Exception:
        return False


def vercel_token() -> tuple[str, str]:
    """Returns (token, source)."""
    if _env("VERCEL_TOKEN"):
        return _env("VERCEL_TOKEN"), "env:VERCEL_TOKEN"
    stored = _read_stored("vercel")
    if stored:
        return stored, "stored"
    cli = _vercel_cli_token()
    if cli:
        return cli, "cli:vercel-login"
    return "", "none"


def cloudflare_token() -> tuple[str, str]:
    for var in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"):
        if _env(var):
            return _env(var), f"env:{var}"
    stored = _read_stored("cloudflare")
    if stored:
        return stored, "stored"
    return "", "none"


def github_token() -> tuple[str, str]:
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        if _env(var):
            return _env(var), f"env:{var}"
    stored = _read_stored("github")
    if stored:
        return stored, "stored"
    cli = _gh_cli_token()
    if cli:
        return cli, "cli:gh-auth"
    return "", "keyless(60/hr)"


# ---------- link + wait flow ----------

_LINKS: dict[str, dict[str, str]] = {
    "github": {
        "url": "https://github.com/settings/tokens/new",
        "instructions": ("Fastest: use the live device login in this same response "
                         "(login.user_code + login.verification_uri) — open the "
                         "verification link, type the code, approve, done; the wait "
                         "notices automatically. Or open the PAT link, create a token "
                         "with scopes repo + workflow, copy it, then accounts_complete "
                         "with request_id and token. Or run `gh auth login --web` in "
                         "your own terminal and the wait will notice."),
    },
    "vercel": {
        "url": "https://vercel.com/account/tokens",
        "instructions": ("Fastest: run `vercel login` in your own terminal (opens "
                         "browser SSO) — accounts_wait notices automatically. Or open "
                         "the link, create a token, copy it, then accounts_complete. "
                         "With HOST_MCP_VERCEL_CLIENT_ID set, accounts_connect also "
                         "starts a one-shot localhost callback and returns login.url."),
    },
    "cloudflare": {
        "url": "https://dash.cloudflare.com/profile/api-tokens",
        "instructions": ("Open the link, use the 'Edit zone DNS' template, copy the "
                         "token, then accounts_complete. Needs Zone:Read minimum; "
                         "DNS:Edit + Zone:Edit for records/purge."),
    },
}

_VALIDATE: dict[str, str] = {
    "github": "GET https://api.github.com/user",
    "vercel": "GET https://api.vercel.com/v2/user",
    "cloudflare": "GET https://api.cloudflare.com/client/v4/user/tokens/verify",
}


def _provider_authed(provider: str) -> bool:
    if provider == "github":
        if _gh_cli_authed():
            return True
        token, _ = github_token()
        if not token:
            return False
        try:
            req = urllib.request.Request(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "User-Agent": _UA,
                         "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=10):
                return True
        except Exception:
            return False
    if provider == "vercel":
        token, _ = vercel_token()
        if not token:
            return False
        try:
            req = urllib.request.Request(
                "https://api.vercel.com/v2/user",
                headers={"Authorization": f"Bearer {token}", "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10):
                return True
        except Exception:
            return False
    if provider == "cloudflare":
        token, _ = cloudflare_token()
        if not token:
            return False
        try:
            req = urllib.request.Request(
                "https://api.cloudflare.com/client/v4/user/tokens/verify",
                headers={"Authorization": f"Bearer {token}", "User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return bool(json.loads(resp.read(2000).decode()).get("success"))
        except Exception:
            return False
    return False


def _req_path(request_id: str) -> pathlib.Path | None:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", request_id or ""):
        return None
    return _REQ_DIR / f"{request_id}.json"


def accounts() -> dict[str, Any]:
    """List linked providers: source + live status. Never returns secrets."""
    out = []
    for provider in _PROVIDERS:
        if provider == "github":
            _, source = github_token()
        elif provider == "vercel":
            _, source = vercel_token()
        else:
            _, source = cloudflare_token()
        out.append({"provider": provider, "source": source,
                    "linked": _provider_authed(provider)})
    return {"ok": True, "accounts": out}


def _start_request(provider: str, extra: dict[str, Any] | None = None) -> str:
    _REQ_DIR.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex[:12]
    doc: dict[str, Any] = {"id": request_id, "provider": provider,
                           "status": "waiting",
                           "created": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                    time.localtime())}
    if extra:
        doc.update(extra)
    (_REQ_DIR / f"{request_id}.json").write_text(json.dumps(doc))
    return request_id


def _github_device_start(request_id: str) -> dict[str, Any] | None:
    """Live GitHub OAuth device flow. Public-client, no user-made app needed."""
    client_id = _env("HOST_MCP_GITHUB_CLIENT_ID") or _GH_DEVICE_CLIENT_ID
    try:
        req = urllib.request.Request(
            "https://github.com/login/device/code",
            data=urllib.parse.urlencode(
                {"client_id": client_id, "scope": _GH_DEVICE_SCOPE}).encode(),
            headers={"Accept": "application/json", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(5000).decode())
        code = str(data.get("device_code") or "")
        user_code = str(data.get("user_code") or "")
        verify = str(data.get("verification_uri") or "")
        interval = max(5, int(data.get("interval", 5)))
        expires = int(data.get("expires_in", 900))
        if not code or not user_code or not verify:
            return None
        path = _REQ_DIR / f"{request_id}.json"
        doc = json.loads(path.read_text())
        doc["login"] = {"kind": "github-device", "client_id": client_id,
                        "device_code": code, "interval": interval,
                        "expires_in": expires}
        path.write_text(json.dumps(doc))
        return {"user_code": user_code, "verification_uri": verify,
                "expires_in": expires, "interval": interval,
                "client_id_note": ("default public client"
                                   if not _env("HOST_MCP_GITHUB_CLIENT_ID")
                                   else "custom HOST_MCP_GITHUB_CLIENT_ID")}
    except Exception:
        return None


def _github_device_poll(request_id: str) -> dict[str, Any] | None:
    """One poll of a github-device login. Returns linked dict, waiting dict, or None."""
    path = _REQ_DIR / f"{request_id}.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    login = doc.get("login") or {}
    if login.get("kind") != "github-device" or not login.get("device_code"):
        return None
    client_id = login.get("client_id") or _GH_DEVICE_CLIENT_ID
    try:
        req = urllib.request.Request(
            "https://github.com/login/oauth/access_token",
            data=urllib.parse.urlencode(
                {"client_id": client_id,
                 "device_code": login["device_code"],
                 "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}).encode(),
            headers={"Accept": "application/json", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(5000).decode())
    except Exception:
        return {"waiting": True}
    if data.get("access_token"):
        token = str(data["access_token"])
        ok, detail = _validate_github(token)
        if ok:
            _write_stored("github", token, label=f"device-flow:{request_id}")
            doc["status"] = "done"
            try:
                path.write_text(json.dumps(doc))
            except OSError:
                pass
            _policy.audit("accounts_complete", {"provider": "github",
                                                "via": "device-flow"}, True)
            return {"linked": True, "detail": detail}
        return {"error": f"Token rejected by github: {detail}"}
    err = str(data.get("error") or "")
    if err in ("authorization_pending", "slow_down"):
        return {"waiting": True}
    if err in ("expired_token", "unsupported_grant_type", "incorrect_client_credentials",
               "incorrect_device_code", "access_denied"):
        return {"error": f"GitHub device flow ended: {err}."}
    return {"waiting": True}


def _vercel_oauth_start(request_id: str) -> dict[str, Any] | None:
    """One-shot localhost callback for Vercel. Needs the user's own client id."""
    client_id = _env("HOST_MCP_VERCEL_CLIENT_ID")
    if not client_id:
        return None
    import http.server
    import threading

    port = max(1024, min(int(_env("HOST_MCP_VERCEL_REDIRECT_PORT") or "8765"), 65535))
    got: dict[str, str] = {}
    server = None
    try:
        import socket as _socket

        with _socket.create_connection(("127.0.0.1", port), timeout=1):
            return {"error": f"Port {port} busy; free it or set HOST_MCP_VERCEL_REDIRECT_PORT."}
    except OSError:
        pass

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in qs:
                got["code"] = qs["code"][0]
                body = ("Authorized in Vercel. Return to Claude; "
                        "the wait notices.").encode()
                self.send_response(200)
            else:
                body = ("Missing ?code= - authorize via the "
                        "login.url first.").encode()
                self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN001, ANN202
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    except OSError as exc:
        return {"error": f"Cannot bind 127.0.0.1:{port}: {exc}."}
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    redirect = f"http://127.0.0.1:{port}/callback"
    url = (_VERCEL_OAUTH_HOST + _VERCEL_OAUTH_PATH + "?" + urllib.parse.urlencode(
        {"client_id": client_id, "redirect_uri": redirect,
         "response_type": "code",
         "scope": "user:read project:read deployment:read"}))
    path = _REQ_DIR / f"{request_id}.json"
    doc = json.loads(path.read_text())
    doc["login"] = {"kind": "vercel-oauth", "client_id": client_id, "port": port,
                    "redirect_uri": redirect}
    path.write_text(json.dumps(doc))
    # Stash the server so accounts_wait can poll the captured code.
    _OAUTH_SERVERS[request_id] = (server, got)
    return {"url": url, "redirect_uri": redirect, "port": port}


_OAUTH_SERVERS: dict[str, Any] = {}


def _vercel_oauth_poll(request_id: str) -> dict[str, Any] | None:
    """Check whether the localhost callback captured ?code=, then exchange it."""
    entry = _OAUTH_SERVERS.get(request_id)
    path = _REQ_DIR / f"{request_id}.json"
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (doc.get("login") or {}).get("kind") != "vercel-oauth":
        return None
    code = (entry[1].get("code") if entry else "") or ""
    if not code:
        return {"waiting": True}
    client_id = doc["login"].get("client_id", "")
    redirect = doc["login"].get("redirect_uri", "")
    try:
        req = urllib.request.Request(
            _VERCEL_OAUTH_HOST + _VERCEL_TOKEN_PATH,
            data=urllib.parse.urlencode(
                {"client_id": client_id, "code": code,
                 "redirect_uri": redirect}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(5000).decode())
    except Exception as exc:
        return {"error": f"Vercel token exchange failed: {type(exc).__name__}: {exc}."}
    finally:
        try:
            if entry:
                entry[0].shutdown()
        except Exception:
            pass
        _OAUTH_SERVERS.pop(request_id, None)
    token = str(data.get("access_token") or "")
    if not token:
        return {"error": f"Vercel refused the code: {str(data)[:200]}."}
    ok, detail = _validate_vercel(token)
    if not ok:
        return {"error": f"Token rejected by vercel: {detail}."}
    _write_stored("vercel", token, label=f"oauth-localhost:{request_id}")
    doc["status"] = "done"
    try:
        path.write_text(json.dumps(doc))
    except OSError:
        pass
    _policy.audit("accounts_complete", {"provider": "vercel",
                                        "via": "oauth-localhost"}, True)
    return {"linked": True, "detail": detail}


def accounts_connect(provider: str) -> dict[str, Any]:
    """Start linking. Returns open-link instructions + request_id, or already:true.

    GitHub also starts a live device flow (user_code + verification link)
    when reachable; Vercel starts a localhost callback when the user set
    HOST_MCP_VERCEL_CLIENT_ID. Cloudflare stays token-link (no OAuth exists).
    """
    provider = provider.strip().lower()
    if provider not in _PROVIDERS:
        return {"ok": False, "error": f"Unknown provider {provider!r}; use github|vercel|cloudflare."}
    if _provider_authed(provider):
        _, source = {"github": github_token, "vercel": vercel_token,
                     "cloudflare": cloudflare_token}[provider]()
        return {"ok": True, "already": True, "provider": provider, "source": source}
    request_id = _start_request(provider)
    info = _LINKS[provider]
    out: dict[str, Any] = {"ok": True, "already": False, "provider": provider,
                           "action": "open-link", "url": info["url"],
                           "instructions": info["instructions"],
                           "request_id": request_id,
                           "validate": _VALIDATE[provider]}
    if provider == "github":
        login = _github_device_start(request_id)
        if login and "error" not in login:
            out["login"] = {"kind": "github-device", **login}
            out["hint"] = (f"Open {login['verification_uri']}, type code "
                           f"{login['user_code']}, approve — then the wait notices.")
        elif login and "error" in login:
            out["login_note"] = login["error"]
    elif provider == "vercel" and _env("HOST_MCP_VERCEL_CLIENT_ID"):
        login = _vercel_oauth_start(request_id)
        if login and "error" not in login:
            out["login"] = {"kind": "vercel-oauth", **login}
            out["url"] = login["url"]
            out["hint"] = ("Open login.url, approve in Vercel, you land back on "
                           "localhost — the wait notices.")
        elif login and "error" in login:
            out["login_note"] = login["error"]
    _policy.audit("accounts_connect", {"provider": provider}, True)
    return out


def accounts_wait(request_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Block until the link request completes. Polls login flows + CLI state.

    Each iteration checks, in order: request already done; live login
    sub-flow (github device poll, vercel localhost callback); CLI login
    appearing in the user's own terminal. Sleeps max(login interval, 5s).
    """
    path = _req_path(request_id)
    if path is None or not path.exists():
        return {"ok": False, "error": f"Unknown request_id: {request_id!r}."}
    try:
        req = json.loads(path.read_text())
    except ValueError:
        return {"ok": False, "error": "Corrupt request file."}
    provider = req.get("provider", "")
    if req.get("status") == "done":
        return {"ok": True, "provider": provider, "linked": True,
                "request_id": request_id}
    interval = 5
    try:
        interval = max(5, int(((req.get("login") or {}).get("interval")) or 5))
    except (TypeError, ValueError):
        interval = 5
    deadline = time.monotonic() + max(5, min(int(timeout_seconds), 600))
    while time.monotonic() < deadline:
        try:
            req = json.loads(path.read_text())
        except ValueError:
            pass
        if req.get("status") == "done":
            return {"ok": True, "provider": provider, "linked": True,
                    "request_id": request_id}
        if provider == "github":
            polled = _github_device_poll(request_id)
            if polled:
                if polled.get("linked"):
                    return {"ok": True, "provider": provider, "linked": True,
                            "request_id": request_id, "via": "github-device",
                            "detail": polled.get("detail", "")}
                if polled.get("error"):
                    return {"ok": False, "provider": provider,
                            "request_id": request_id, "error": polled["error"]}
        elif provider == "vercel":
            polled = _vercel_oauth_poll(request_id)
            if polled:
                if polled.get("linked"):
                    return {"ok": True, "provider": provider, "linked": True,
                            "request_id": request_id, "via": "vercel-oauth",
                            "detail": polled.get("detail", "")}
                if polled.get("error"):
                    return {"ok": False, "provider": provider,
                            "request_id": request_id, "error": polled["error"]}
        if _provider_authed(provider):
            # User authorized in their own terminal (vercel login / gh login).
            req["status"] = "done"
            try:
                path.write_text(json.dumps(req))
            except OSError:
                pass
            return {"ok": True, "provider": provider, "linked": True,
                    "request_id": request_id, "via": "cli-login-detected"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    return {"ok": True, "waiting": True, "provider": provider,
            "request_id": request_id,
            "hint": "Still waiting. User opens the link, authorizes, then accounts_complete — or call accounts_wait again."}


def accounts_complete(request_id: str, token: str) -> dict[str, Any]:
    """Validate a pasted token against the real API, store chmod 600, close request."""
    path = _req_path(request_id)
    if path is None or not path.exists():
        return {"ok": False, "error": f"Unknown request_id: {request_id!r}."}
    token = token.strip()
    if len(token) < 8:
        return {"ok": False, "error": "Token too short; paste the full token."}
    try:
        req = json.loads(path.read_text())
    except ValueError:
        return {"ok": False, "error": "Corrupt request file."}
    provider = req.get("provider", "")
    # Validate before storing: must prove itself against the provider.
    if provider == "github":
        probe_ok = _validate_github(token)
    elif provider == "vercel":
        probe_ok = _validate_vercel(token)
    elif provider == "cloudflare":
        probe_ok = _validate_cloudflare(token)
    else:
        return {"ok": False, "error": f"Unknown provider {provider!r}."}
    if not probe_ok[0]:
        _policy.audit("accounts_complete", {"provider": provider}, False)
        return {"ok": False, "error": f"Token rejected by {provider}: {probe_ok[1]}"}
    _write_stored(provider, token, label=f"via-accounts_complete:{request_id}")
    req["status"] = "done"
    try:
        path.write_text(json.dumps(req))
    except OSError:
        pass
    _policy.audit("accounts_complete", {"provider": provider}, True)
    return {"ok": True, "provider": provider, "linked": True,
            "request_id": request_id, "detail": probe_ok[1][:200]}


def _validate_github(token: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "User-Agent": _UA,
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            login = json.loads(resp.read(5000).decode()).get("login", "?")
            return True, f"login={login}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _validate_vercel(token: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://api.vercel.com/v2/user",
            headers={"Authorization": f"Bearer {token}", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            user = json.loads(resp.read(5000).decode()).get("user", {})
            return True, f"user={user.get('username', '?')}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _validate_cloudflare(token: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            "https://api.cloudflare.com/client/v4/user/tokens/verify",
            headers={"Authorization": f"Bearer {token}", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read(5000).decode())
            if data.get("success"):
                return True, f"id={data.get('result', {}).get('id', '?')}"
            return False, str(data.get("errors", []))[:200]
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def accounts_remove(provider: str) -> dict[str, Any]:
    """Delete a stored token. Prompts (destructive). CLI logins untouched."""
    provider = provider.strip().lower()
    if provider not in _PROVIDERS:
        return {"ok": False, "error": f"Unknown provider {provider!r}."}
    path = _ACCT_DIR / f"{provider}.json"
    if not path.exists():
        return {"ok": False, "error": f"No stored token for {provider}."}
    try:
        path.unlink()
        _policy.audit("accounts_remove", {"provider": provider}, True)
        return {"ok": True, "provider": provider, "removed": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------- Vercel management (auto token: env > stored > CLI) ----------

def _vc_api(path: str, method: str = "GET",
            payload: dict | None = None) -> dict[str, Any]:
    token, source = vercel_token()
    if not token:
        return {"ok": False, "error": "Vercel not linked. Run accounts_connect('vercel'): `vercel login` or a token from https://vercel.com/account/tokens.",
                "action": "open-link", "url": _LINKS["vercel"]["url"]}
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request("https://api.vercel.com" + path, data=data,
                                     method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json",
                                              "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(3_000_000).decode(errors="replace")
            return {"ok": True, "status": resp.status,
                    "data": json.loads(body) if body.strip() else None,
                    "auth": source}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2000).decode(errors="replace")[:400]
        except Exception:
            detail = str(getattr(exc, "reason", exc))[:400]
        if exc.code in (401, 403):
            return {"ok": False, "error": f"Vercel auth rejected ({exc.code}); re-link via accounts_connect('vercel').",
                    "detail": detail[:200]}
        return {"ok": False, "error": f"Vercel HTTP {exc.code}: {detail[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def vercel_projects(limit: int = 20) -> dict[str, Any]:
    """List Vercel projects (name, id, production URL). Auto token."""
    out = _vc_api(f"/v9/projects?limit={max(1, min(int(limit), 100))}")
    if not out.get("ok"):
        return out
    return {"ok": True, "auth": out.get("auth"),
            "projects": [{"name": p.get("name"), "id": p.get("id"),
                          "url": p.get("latestProductionUrl") or
                          (p.get("alias") or [None])[0]}
                         for p in (out["data"].get("projects", []) or [])]}


def vercel_deployments(project: str = "", limit: int = 10) -> dict[str, Any]:
    """List deployments, optionally filtered by project name or id."""
    params = {"limit": str(max(1, min(int(limit), 100)))}
    if project.strip():
        q = project.strip()
        if q.startswith("prj_"):
            params["projectId"] = q
        else:
            proj = _vc_api("/v9/projects?limit=100")
            if not proj.get("ok"):
                return proj
            match = next((p for p in proj["data"].get("projects", [])
                          if p.get("name") == q), None)
            if not match:
                return {"ok": False, "error": f"No Vercel project named {q!r}."}
            params["projectId"] = match["id"]
    out = _vc_api("/v6/deployments?" + urllib.parse.urlencode(params))
    if not out.get("ok"):
        return out
    rows = []
    for d in (out["data"].get("deployments", []) or []):
        rows.append({"id": d.get("uid") or d.get("id"), "name": d.get("name"),
                     "state": d.get("state") or d.get("readyState"),
                     "target": d.get("target"),
                     "url": d.get("url"), "created": d.get("createdAt")})
    return {"ok": True, "auth": out.get("auth"), "deployments": rows}


def vercel_inspect(deployment: str) -> dict[str, Any]:
    """Full deployment detail: aliases, state, regions, build env.

    Accepts a deployment UID (dpl_...), a full *.vercel.app URL, or a
    project name (resolves to its latest deployment automatically).
    """
    deployment = deployment.strip()
    if not deployment:
        return {"ok": False, "error": "deployment id, url, or project name required."}
    dep_id = deployment.split(".vercel.app")[0]
    if not dep_id.startswith(("dpl_", "dpl-")) and "." not in dep_id and "/" not in dep_id:
        found = vercel_deployments(dep_id, 1)
        if found.get("ok") and found.get("deployments"):
            dep_id = found["deployments"][0]["id"]
        else:
            return {"ok": False, "error": f"No deployment for project {deployment!r}: {found.get('error', 'none found')}"}
    out = _vc_api(f"/v13/deployments/{urllib.parse.quote(dep_id)}")
    if not out.get("ok"):
        return out
    d = out["data"]
    return {"ok": True, "id": d.get("id"), "name": d.get("name"),
            "state": d.get("readyState"), "url": d.get("url"),
            "aliases": (d.get("alias") or [])[:5], "regions": d.get("regions"),
            "created": d.get("createdAt"),
            "creator": (d.get("creator") or {}).get("username")}


def vercel_logs(deployment: str, limit: int = 30) -> dict[str, Any]:
    """Build/runtime event tail. Accepts UID, vercel.app URL, or project name."""
    deployment = deployment.strip()
    if not deployment:
        return {"ok": False, "error": "deployment id, url, or project name required."}
    dep_id = deployment.split(".vercel.app")[0]
    if not dep_id.startswith(("dpl_", "dpl-")) and "." not in dep_id and "/" not in dep_id:
        found = vercel_deployments(dep_id, 1)
        if found.get("ok") and found.get("deployments"):
            dep_id = found["deployments"][0]["id"]
        else:
            return {"ok": False, "error": f"No deployment for project {deployment!r}."}
    out = _vc_api(f"/v2/deployments/{urllib.parse.quote(dep_id)}/events?"
                  + urllib.parse.urlencode({"limit": str(max(1, min(int(limit), 100)))}))
    if not out.get("ok"):
        return out
    lines = []
    for ev in (out["data"] if isinstance(out["data"], list) else []):
        payload = ev.get("payload") or {}
        text = payload.get("text") or payload.get("name") or ev.get("type", "")
        if text:
            lines.append(str(text)[:300])
    return {"ok": True, "deployment": dep_id, "events": lines[-limit:]}


def vercel_redeploy(deployment: str, target: str = "") -> dict[str, Any]:
    """Rebuild a deployment from its project. Prompts (creates live deploys)."""
    if (blocked := _gate("vercel_redeploy", "vercel")) is not None:
        return blocked
    deployment = deployment.strip()
    if not deployment:
        return {"ok": False, "error": "deployment id or url required."}
    info = vercel_inspect(deployment)
    if not info.get("ok"):
        return info
    body: dict[str, Any] = {"deploymentId": info["id"], "name": info["name"]}
    if target.strip().lower() in ("production", "preview", "staging"):
        body["target"] = target.strip().lower()
    out = _vc_api("/v13/deployments", "POST", body)
    if not out.get("ok"):
        _policy.audit("vercel_redeploy", {"deployment": info["id"]}, False)
        return out
    d = out["data"] or {}
    _policy.audit("vercel_redeploy",
                  {"deployment": info["id"], "new": d.get("id", "?")}, True)
    return {"ok": True, "old": info["id"], "new": d.get("id"),
            "url": d.get("url"), "state": d.get("readyState")}


# ---------- Cloudflare management (token-only; no CLI exists) ----------

def _cf_api(path: str, method: str = "GET",
            payload: dict | None = None) -> dict[str, Any]:
    token, source = cloudflare_token()
    if not token:
        return {"ok": False, "error": "Cloudflare not linked. Run accounts_connect('cloudflare') for the token link (Zone:Read + DNS:Edit template).",
                "action": "open-link", "url": _LINKS["cloudflare"]["url"]}
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request("https://api.cloudflare.com/client/v4" + path,
                                     data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json",
                                              "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read(3_000_000).decode(errors="replace"))
            if not body.get("success"):
                return {"ok": False, "error": f"Cloudflare: {str(body.get('errors', []))[:300]}"}
            return {"ok": True, "result": body.get("result"), "auth": source}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2000).decode(errors="replace")[:300]
        except Exception:
            detail = str(getattr(exc, "reason", exc))[:300]
        if exc.code in (401, 403):
            return {"ok": False, "error": f"Cloudflare auth rejected ({exc.code}); check token scopes via accounts_connect('cloudflare').",
                    "detail": detail[:200]}
        return {"ok": False, "error": f"Cloudflare HTTP {exc.code}: {detail[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cloudflare_zones(name: str = "") -> dict[str, Any]:
    """List zones (id, name, status, plan). Filter by substring."""
    out = _cf_api("/zones?per_page=50")
    if not out.get("ok"):
        return out
    rows = [{"id": z.get("id"), "name": z.get("name"), "status": z.get("status"),
             "plan": (z.get("plan") or {}).get("name")}
            for z in (out["result"] or [])]
    if name.strip():
        rows = [r for r in rows if name.strip().lower() in (r["name"] or "").lower()]
    return {"ok": True, "auth": out.get("auth"), "zones": rows}


def _cf_zone_id(zone: str) -> tuple[str, str]:
    zone = zone.strip()
    if re.fullmatch(r"[a-f0-9]{32}", zone):
        return zone, ""
    out = cloudflare_zones(zone)
    if not out.get("ok"):
        return "", out["error"]
    exact = [z for z in out["zones"] if z["name"] == zone]
    pick = (exact or out["zones"])
    if not pick:
        return "", f"No Cloudflare zone matching {zone!r}."
    if len(pick) > 1 and not exact:
        names = [z["name"] for z in pick[:5]]
        return "", f"Ambiguous zone {zone!r}: {names}."
    return pick[0]["id"], ""


def cloudflare_account() -> dict[str, Any]:
    """First account (id, name). Needs Account:Read on the token."""
    out = _cf_api("/accounts?per_page=5")
    if not out.get("ok"):
        return out
    rows = [{"id": a.get("id"), "name": a.get("name")} for a in (out["result"] or [])]
    return {"ok": True, "auth": out.get("auth"), "accounts": rows}


def cloudflare_dns(zone: str, dtype: str = "", name: str = "") -> dict[str, Any]:
    """List DNS records for a zone (name or id). Filter by type/name."""
    zid, err = _cf_zone_id(zone)
    if err:
        return {"ok": False, "error": err}
    params = {"per_page": "100"}
    if dtype.strip():
        params["type"] = dtype.strip().upper()
    if name.strip():
        params["name"] = name.strip()
    out = _cf_api(f"/zones/{zid}/dns_records?" + urllib.parse.urlencode(params))
    if not out.get("ok"):
        return out
    return {"ok": True, "zone_id": zid,
            "records": [{"id": r.get("id"), "type": r.get("type"),
                         "name": r.get("name"), "content": r.get("content"),
                         "ttl": r.get("ttl"), "proxied": r.get("proxied")}
                        for r in (out["result"] or [])]}


def cloudflare_dns_create(zone: str, dtype: str, name: str, content: str,
                          ttl: int = 3600, proxied: bool = False) -> dict[str, Any]:
    """Create a DNS record. Prompts."""
    if (blocked := _gate("cloudflare_dns_create", "cloudflare")) is not None:
        return blocked
    zid, err = _cf_zone_id(zone)
    if err:
        return {"ok": False, "error": err}
    dtype = dtype.strip().upper()
    if dtype not in ("A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"):
        return {"ok": False, "error": f"Unsupported type {dtype!r}."}
    if not name.strip() or not content.strip():
        return {"ok": False, "error": "name and content required."}
    out = _cf_api(f"/zones/{zid}/dns_records", "POST",
                  {"type": dtype, "name": name.strip(), "content": content.strip(),
                   "ttl": max(60, min(int(ttl), 86400)), "proxied": bool(proxied)})
    if not out.get("ok"):
        _policy.audit("cloudflare_dns_create", {"zone": zid}, False)
        return out
    _policy.audit("cloudflare_dns_create",
                  {"zone": zid, "type": dtype, "name": name[:120]}, True)
    r = out["result"] or {}
    return {"ok": True, "id": r.get("id"), "type": r.get("type"),
            "name": r.get("name"), "content": r.get("content")}


def cloudflare_dns_delete(zone: str, record_id: str) -> dict[str, Any]:
    """Delete a DNS record by id. Prompts."""
    if (blocked := _gate("cloudflare_dns_delete", "cloudflare")) is not None:
        return blocked
    zid, err = _cf_zone_id(zone)
    if err:
        return {"ok": False, "error": err}
    if not re.fullmatch(r"[a-f0-9]{32}", (record_id or "").strip()):
        return {"ok": False, "error": "record_id must be a 32-hex id (see cloudflare_dns)."}
    out = _cf_api(f"/zones/{zid}/dns_records/{record_id.strip()}", "DELETE")
    if not out.get("ok"):
        _policy.audit("cloudflare_dns_delete", {"zone": zid}, False)
        return out
    _policy.audit("cloudflare_dns_delete", {"zone": zid, "record": record_id[:12]}, True)
    return {"ok": True, "zone_id": zid, "deleted": record_id.strip()}


def cloudflare_purge(zone: str) -> dict[str, Any]:
    """Purge everything in a zone cache. Prompts."""
    if (blocked := _gate("cloudflare_purge", "cloudflare")) is not None:
        return blocked
    zid, err = _cf_zone_id(zone)
    if err:
        return {"ok": False, "error": err}
    out = _cf_api(f"/zones/{zid}/purge_cache", "POST", {"purge_everything": True})
    if not out.get("ok"):
        _policy.audit("cloudflare_purge", {"zone": zid}, False)
        return out
    _policy.audit("cloudflare_purge", {"zone": zid}, True)
    return {"ok": True, "zone_id": zid, "purged": True}


def register(mcp) -> None:
    """Register account tools. Follows repo wrapper convention (no impl shadowing)."""
    import sys as _sys

    _self = _sys.modules[__name__]
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _WR = _TA(read_only_hint=False, destructive_hint=False,
              idempotent_hint=False, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    @mcp.tool(title="Linked accounts", annotations=_RO)
    def accounts() -> dict[str, Any]:
        """Linked providers with source + live status. Never returns secrets."""
        return _self.accounts()

    @mcp.tool(title="Connect account", annotations=_WR)
    def accounts_connect(provider: str) -> dict[str, Any]:
        """Start linking github|vercel|cloudflare. Returns open-link + request_id (or already:true)."""
        return _self.accounts_connect(provider)

    @mcp.tool(title="Wait for link", annotations=_RO)
    def accounts_wait(request_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
        """Block until the user authorizes (link opened) or timeout. Polls 5s."""
        return _self.accounts_wait(request_id, timeout_seconds)

    @mcp.tool(title="Complete link", annotations=_WR)
    def accounts_complete(request_id: str, token: str) -> dict[str, Any]:
        """Validate a pasted token live, store chmod 600, close request."""
        return _self.accounts_complete(request_id, token)

    @mcp.tool(title="Remove account", annotations=_MUT)
    def accounts_remove(provider: str) -> dict[str, Any]:
        """Delete a stored token. Prompts. CLI logins untouched."""
        return _self.accounts_remove(provider)

    @mcp.tool(title="Vercel projects", annotations=_RO)
    def vercel_projects(limit: int = 20) -> dict[str, Any]:
        """List Vercel projects. Auto token (env > stored > CLI login)."""
        return _self.vercel_projects(limit)

    @mcp.tool(title="Vercel deployments", annotations=_RO)
    def vercel_deployments(project: str = "", limit: int = 10) -> dict[str, Any]:
        """List deployments, optional project name/id filter."""
        return _self.vercel_deployments(project, limit)

    @mcp.tool(title="Vercel inspect", annotations=_RO)
    def vercel_inspect(deployment: str) -> dict[str, Any]:
        """Deployment detail: aliases, state, regions, creator."""
        return _self.vercel_inspect(deployment)

    @mcp.tool(title="Vercel logs", annotations=_RO)
    def vercel_logs(deployment: str, limit: int = 30) -> dict[str, Any]:
        """Build/runtime event tail for a deployment."""
        return _self.vercel_logs(deployment, limit)

    @mcp.tool(title="Vercel redeploy", annotations=_MUT)
    def vercel_redeploy(deployment: str, target: str = "") -> dict[str, Any]:
        """Rebuild a deployment. Prompts (creates live deploys)."""
        return _self.vercel_redeploy(deployment, target)

    @mcp.tool(title="Cloudflare zones", annotations=_RO)
    def cloudflare_zones(name: str = "") -> dict[str, Any]:
        """List zones (id, name, status, plan). Needs token."""
        return _self.cloudflare_zones(name)

    @mcp.tool(title="Cloudflare account", annotations=_RO)
    def cloudflare_account() -> dict[str, Any]:
        """First account id/name. Needs Account:Read."""
        return _self.cloudflare_account()

    @mcp.tool(title="Cloudflare DNS", annotations=_RO)
    def cloudflare_dns(zone: str, dtype: str = "", name: str = "") -> dict[str, Any]:
        """List DNS records for a zone (name or id)."""
        return _self.cloudflare_dns(zone, dtype, name)

    @mcp.tool(title="Cloudflare DNS create", annotations=_MUT)
    def cloudflare_dns_create(zone: str, dtype: str, name: str, content: str,
                              ttl: int = 3600, proxied: bool = False) -> dict[str, Any]:
        """Create a DNS record. Prompts."""
        return _self.cloudflare_dns_create(zone, dtype, name, content, ttl, proxied)

    @mcp.tool(title="Cloudflare DNS delete", annotations=_MUT)
    def cloudflare_dns_delete(zone: str, record_id: str) -> dict[str, Any]:
        """Delete a DNS record by id. Prompts."""
        return _self.cloudflare_dns_delete(zone, record_id)

    @mcp.tool(title="Cloudflare purge", annotations=_MUT)
    def cloudflare_purge(zone: str) -> dict[str, Any]:
        """Purge a zone cache. Prompts."""
        return _self.cloudflare_purge(zone)
