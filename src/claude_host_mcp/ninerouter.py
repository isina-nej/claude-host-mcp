"""9router suite for claude-host-mcp: use the local AI gateway as a tool.

9router (npm 0.5.69) runs on this host at 127.0.0.1:20128 and exposes an
OpenAI-compatible API plus management endpoints. This module turns it into
first-class MCP tools so agents can:

  discovery  models/combos/providers/usage without touching sqlite
  chat       ask any combo/model, single-shot or streamed
  multi      fan the same prompt out to N models in parallel (multi-agent)
  media      image generation via direct provider models, TTS/STT, search

Auth: NINEROUTER_API_KEY env wins; else auto-reads the first ACTIVE key
from ~/.9router/db/data.sqlite (same keys the dashboard uses). No new
setup for the owner. Rate-limit (429/Retry-After) and provider errors
are returned as {ok:false} dicts, never raised.

Multi-agent: fanout() runs threads, one per model, each with its own
timeout. Use for judge/ensemble patterns: same prompt, N answers, then
compare. Concurrency capped at 6 to protect free-tier quotas.

ponytail: video/TTS/embeddings shapes were probed live and vary by host
(verified: chat+stream+images-direct+search work; video-gen/TTS-route/
embeddings error here). Wrappers pass provider errors through verbatim
so they light up automatically when the host supports them.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import policy as _policy

BASE = os.environ.get("NINEROUTER_BASE_URL", "http://127.0.0.1:20128").rstrip("/")
_UA = "claude-host-mcp/0.7"
_DB = pathlib.Path.home() / ".9router/db/data.sqlite"
_FANOUT_CAP = 6


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _api_key() -> str:
    key = _env("NINEROUTER_API_KEY")
    if key:
        return key
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT key FROM apiKeys WHERE isActive=1 ORDER BY rowid LIMIT 1"
            ).fetchone()
            return (row[0] if row else "").strip()
        finally:
            con.close()
    except Exception:
        return ""


def _call(method: str, path: str, payload: dict | None = None,
          timeout: int = 30, stream: bool = False):
    """Low-level HTTP. Returns (ok, parsed-or-raw, status)."""
    key = _api_key()
    if not key:
        return False, {"ok": False, "error": "No 9router API key. Start 9router or set NINEROUTER_API_KEY."}, 0
    try:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            BASE + path, data=data, method=method,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json", "User-Agent": _UA})
        if stream:
            return True, req, 200  # caller opens it
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(10_000_000).decode(errors="replace")
            # 9router pads JSON with whitespace/newlines and appends `data: [DONE]`.
            cleaned = raw.strip()
            if cleaned.endswith("data: [DONE]"):
                cleaned = cleaned[: -len("data: [DONE]")].strip()
            try:
                return True, json.loads(cleaned) if cleaned else {}, resp.status
            except ValueError:
                # last resort: first {...} block
                start, end = cleaned.find("{"), cleaned.rfind("}")
                if start != -1 and end > start:
                    try:
                        return True, json.loads(cleaned[start:end + 1]), resp.status
                    except ValueError:
                        pass
                return True, {"raw": raw[:20000]}, resp.status
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(4000).decode(errors="replace")[:800]
        except Exception:
            detail = str(getattr(exc, "reason", exc))[:800]
        if exc.code == 429:
            try:
                retry = exc.headers.get("Retry-After", "")
            except Exception:
                retry = ""
            return False, {"ok": False, "error": f"9router rate-limited (429). Retry after {retry or '?'}s.",
                           "detail": detail[:300]}, 429
        return False, {"ok": False, "error": f"9router HTTP {exc.code}: {detail[:300]}"}, exc.code
    except Exception as exc:
        return False, {"ok": False, "error": f"{type(exc).__name__}: {exc}. Is 9router up at {BASE}?"}, 0


def _gate(tool: str, family: str = "ninerouter"):
    from .server import _gate as _g

    return _g(tool, family)


# ---------- discovery (all keyless-safe reads, no prompt) ----------

def status() -> dict[str, Any]:
    """9router health + version. No key needed."""
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=10) as resp:
            health = json.loads(resp.read(1000).decode())
        with urllib.request.urlopen(BASE + "/api/version", timeout=10) as resp:
            ver = json.loads(resp.read(2000).decode())
        return {"ok": True, "base": BASE, "healthy": health.get("ok"),
                "version": ver.get("currentVersion"), "update": ver.get("hasUpdate")}
    except Exception as exc:
        return {"ok": False, "error": f"9router down at {BASE}: {type(exc).__name__}: {exc}"}


def models() -> dict[str, Any]:
    """List combos + provider models routable through 9router."""
    ok, data, _ = _call("GET", "/api/v1/models", timeout=15)
    if not ok:
        return data
    items = data.get("data", []) if isinstance(data, dict) else []
    return {"ok": True, "count": len(items),
            "models": [{"id": m.get("id"), "owner": m.get("owned_by")} for m in items]}


def combos() -> dict[str, Any]:
    """List named combos (sina-pro, image, FastImg...) with member models."""
    ok, data, _ = _call("GET", "/api/combos", timeout=15)
    if not ok:
        return data
    items = data.get("combos", []) if isinstance(data, dict) else []
    return {"ok": True, "count": len(items),
            "combos": [{"name": c.get("name"), "kind": c.get("kind"),
                        "models": (c.get("models") or [])[:12]} for c in items]}


def providers() -> dict[str, Any]:
    """Provider connections with health (testStatus, lastUsedAt, no secrets)."""
    ok, data, _ = _call("GET", "/api/providers", timeout=15)
    if not ok:
        return data
    out = []
    for c in (data.get("connections", []) if isinstance(data, dict) else []):
        out.append({k: c.get(k) for k in
                    ("provider", "name", "priority", "isActive", "testStatus",
                     "lastUsedAt", "consecutiveUseCount", "lastError")})
        if isinstance(out[-1].get("lastError"), str):
            out[-1]["lastError"] = out[-1]["lastError"][:200]
    return {"ok": True, "count": len(out), "connections": out}


def usage() -> dict[str, Any]:
    """Totals: requests, prompt/completion/cached tokens, cost, per-provider."""
    ok, data, _ = _call("GET", "/api/usage/stats", timeout=15)
    if not ok:
        return data
    if not isinstance(data, dict):
        return {"ok": False, "error": "Unexpected usage payload."}
    data = dict(data)
    data.pop("byProvider", None)
    ok2, data2, _ = _call("GET", "/api/usage/stats", timeout=15)
    by = (data2.get("byProvider", {}) if isinstance(data2, dict) else {})
    data["ok"] = True
    data["providers"] = sorted(by.keys())[:30]
    return data


# ---------- chat ----------

def _messages(prompt: str, system: str = "") -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if system.strip():
        msgs.append({"role": "system", "content": system.strip()[:4000]})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def chat(model: str, prompt: str, system: str = "", max_tokens: int = 1024,
         temperature: float = 0.7, timeout_seconds: int = 120) -> dict[str, Any]:
    """Ask any 9router combo/model. Single-shot chat completion."""
    model, prompt = model.strip(), prompt.strip()
    if not model or not prompt:
        return {"ok": False, "error": "model and prompt required."}
    if len(prompt) > 60000:
        return {"ok": False, "error": "prompt capped at 60000 chars."}
    timeout = max(10, min(int(timeout_seconds), 600))
    ok, data, _ = _call("POST", "/api/v1/chat/completions", {
        "model": model, "messages": _messages(prompt, system),
        "max_tokens": max(1, min(int(max_tokens), 32000)),
        "temperature": max(0.0, min(float(temperature), 2.0))}, timeout=timeout)
    if not ok:
        return data
    try:
        ch = (data.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        usage = data.get("usage") or {}
        text = msg.get("content")
        if not text and isinstance(msg.get("reasoning"), str) and msg["reasoning"].strip():
            text = msg["reasoning"]  # reasoning-only models put the answer there
        out: dict[str, Any] = {"ok": True, "model": data.get("model"),
                               "provider": data.get("provider"),
                               "text": text,
                               "finish": ch.get("finish_reason")}
        if isinstance(msg.get("reasoning"), str) and msg["reasoning"] != text:
            out["reasoning"] = msg["reasoning"][:2000]
        out["usage"] = {k: usage.get(k) for k in
                        ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}
        return out
    except Exception as exc:
        return {"ok": False, "error": f"Bad chat payload: {exc}"}


def chat_stream(model: str, prompt: str, system: str = "", max_tokens: int = 1024,
                timeout_seconds: int = 120) -> dict[str, Any]:
    """Ask with SSE streaming; returns full concatenated text (no live partials over MCP)."""
    model, prompt = model.strip(), prompt.strip()
    if not model or not prompt:
        return {"ok": False, "error": "model and prompt required."}
    timeout = max(10, min(int(timeout_seconds), 600))
    ok, req, _ = _call("POST", "/api/v1/chat/completions", {
        "model": model, "messages": _messages(prompt, system), "stream": True,
        "max_tokens": max(1, min(int(max_tokens), 32000))}, timeout=timeout, stream=True)
    if not ok:
        return req
    key = _api_key()
    text_parts: list[str] = []
    final_model, provider = model, ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except ValueError:
                    continue
                final_model = evt.get("model", final_model)
                provider = evt.get("provider", provider)
                delta = ((evt.get("choices") or [{}])[0].get("delta") or {})
                if delta.get("content"):
                    text_parts.append(delta["content"])
        void = key  # key already embedded in req headers
        return {"ok": True, "model": final_model, "provider": provider or None,
                "text": "".join(text_parts)[:60000], "streamed": True, "void": void[:0]}
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2000).decode(errors="replace")[:400]
        except Exception:
            detail = str(getattr(exc, "reason", exc))[:400]
        return {"ok": False, "error": f"9router HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def fanout(models: list[str], prompt: str, system: str = "", max_tokens: int = 512,
           timeout_seconds: int = 180) -> dict[str, Any]:
    """Ask N models the same prompt in parallel. Multi-agent judge/ensemble primitive.

    Returns per-model {text|error}. Concurrency capped at 6; each leg capped
    at timeout_seconds. Ideal: draft with cheap combo, judge with strong one.
    """
    models = [m.strip() for m in (models or []) if m and m.strip()][: _FANOUT_CAP]
    prompt = prompt.strip()
    if not models:
        return {"ok": False, "error": "At least one model required (max 6)."}
    if not prompt:
        return {"ok": False, "error": "Empty prompt."}
    results: dict[str, Any] = {}
    lock = threading.Lock()

    def leg(model: str) -> None:
        r = chat(model, prompt, system, max_tokens, timeout_seconds=timeout_seconds)
        with lock:
            results[model] = r

    threads = [threading.Thread(target=leg, args=(m,), daemon=True) for m in models]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max(10, min(int(timeout_seconds), 600)) + 10)
    for m in models:
        results.setdefault(m, {"ok": False, "error": "Timed out waiting for leg."})
    _policy.audit("nine_fanout", {"models": models, "prompt_chars": len(prompt)}, True)
    ok_count = sum(1 for r in results.values() if isinstance(r, dict) and r.get("ok"))
    return {"ok": ok_count > 0, "prompt_chars": len(prompt),
            "succeeded": ok_count, "total": len(models), "results": results}


# ---------- media + search ----------

def image(prompt: str, model: str = "", n: int = 1,
          size: str = "1024x1024", timeout_seconds: int = 120) -> dict[str, Any]:
    """Generate images via 9router. Default model auto-picks first FastImg member.

    Returns b64_json list (MCP-safe; caller saves bytes). Verified live with
    cf/@cf/bytedance/stable-diffusion-xl-lightning. Gemini image models may
    403 depending on provider quota — error passes through verbatim.
    """
    prompt = prompt.strip()
    if not prompt:
        return {"ok": False, "error": "Empty prompt."}
    if not model.strip():
        c = combos()
        pick = ""
        if c.get("ok"):
            for combo in c["combos"]:
                if combo.get("kind") == "image-proxy" and combo.get("models"):
                    pick = combo["models"][0]
                    break
        model = pick or "cf/@cf/bytedance/stable-diffusion-xl-lightning"
    timeout = max(15, min(int(timeout_seconds), 600))
    ok, data, _ = _call("POST", "/api/v1/images/generations", {
        "model": model.strip(), "prompt": prompt[:4000],
        "n": max(1, min(int(n), 4)), "size": size}, timeout=timeout)
    if not ok:
        return data
    try:
        items = data.get("data", []) if isinstance(data, dict) else []
        return {"ok": True, "model": model, "count": len(items),
                "images": [{"b64_json": (it.get("b64_json") or "")[:0] + (it.get("b64_json") or ""),
                            "bytes": len(it.get("b64_json") or "") * 3 // 4}
                           for it in items]}
    except Exception as exc:
        return {"ok": False, "error": f"Bad image payload: {exc}"}


def tts(text: str, voice: str = "alloy", model: str = "",
        timeout_seconds: int = 120) -> dict[str, Any]:
    """Text-to-speech via 9router audio route. Shape varies by host; errors pass through."""
    text = text.strip()
    if not text:
        return {"ok": False, "error": "Empty text."}
    if len(text) > 8000:
        return {"ok": False, "error": "text capped at 8000 chars."}
    timeout = max(15, min(int(timeout_seconds), 600))
    ok, data, _ = _call("POST", "/api/v1/audio/speech", {
        "model": model.strip() or "sina-economy",
        "input": text, "voice": voice}, timeout=timeout)
    if not ok:
        return data
    if isinstance(data, dict) and data.get("raw"):
        return {"ok": True, "audio_b64": data["raw"][:0], "note": "Binary audio; use HTTP directly for bytes."}
    return {"ok": True, "result": data}


def stt(audio_b64: str, model: str = "", timeout_seconds: int = 180) -> dict[str, Any]:
    """Speech-to-text via 9router transcriptions route. Errors pass through verbatim."""
    if not audio_b64.strip():
        return {"ok": False, "error": "Empty audio_b64."}
    timeout = max(15, min(int(timeout_seconds), 600))
    ok, data, _ = _call("POST", "/api/v1/audio/transcriptions", {
        "model": model.strip() or "sina-economy",
        "audio": audio_b64[:0] + audio_b64}, timeout=timeout)
    return data if isinstance(data, dict) else {"ok": False, "error": "Bad STT payload."}


def embeddings(texts: list[str], model: str = "") -> dict[str, Any]:
    """Embeddings via 9router. Shape varies by host; errors pass through verbatim."""
    texts = [t for t in (texts or []) if t and t.strip()][:32]
    if not texts:
        return {"ok": False, "error": "At least one text required (max 32)."}
    ok, data, _ = _call("POST", "/api/v1/embeddings", {
        "model": model.strip() or "sina-economy",
        "input": texts}, timeout=60)
    return data if isinstance(data, dict) else {"ok": False, "error": "Bad embeddings payload."}


def nine_search(query: str, provider: str = "searchapi",
                count: int = 5) -> dict[str, Any]:
    """Web search THROUGH 9router (uses its searchapi connection). Keyless for you."""
    query = query.strip()
    if not query:
        return {"ok": False, "error": "Empty query."}
    ok, data, _ = _call("POST", "/api/v1/search", {
        "provider": provider, "query": query}, timeout=30)
    if not ok:
        return data
    if not isinstance(data, dict):
        return {"ok": False, "error": "Bad search payload."}
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    return {"ok": True, "provider": data.get("provider", provider),
            "results": [{"title": r.get("title", "")[:150], "url": r.get("url", ""),
                         "snippet": r.get("snippet", "")[:300]} for r in results[:count]]}


def video(prompt: str, model: str = "", timeout_seconds: int = 300) -> dict[str, Any]:
    """Video generation via 9router. Speculative: verified 400s on this host; kept for hosts that support it."""
    prompt = prompt.strip()
    if not prompt:
        return {"ok": False, "error": "Empty prompt."}
    timeout = max(30, min(int(timeout_seconds), 900))
    ok, data, _ = _call("POST", "/api/v1/videos/generations", {
        "model": model.strip() or "sina-economy", "prompt": prompt[:2000]}, timeout=timeout)
    return data if isinstance(data, dict) else {"ok": False, "error": "Bad video payload."}


def register(mcp) -> None:
    """Register 9router tools + resources. Follows repo wrapper convention."""
    import sys as _sys

    _self = _sys.modules[__name__]
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _RONET = _TA(read_only_hint=True, open_world_hint=True)
    _WR = _TA(read_only_hint=False, destructive_hint=False,
              idempotent_hint=False, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    @mcp.tool(title="9router status", annotations=_RO)
    def nine_status() -> dict[str, Any]:
        """Gateway health + version. No key needed."""
        return _self.status()

    @mcp.tool(title="9router models", annotations=_RO)
    def nine_models() -> dict[str, Any]:
        """List routable combos + provider models."""
        return _self.models()

    @mcp.tool(title="9router combos", annotations=_RO)
    def nine_combos() -> dict[str, Any]:
        """Combos with member models (sina-pro, image, FastImg...)."""
        return _self.combos()

    @mcp.tool(title="9router providers", annotations=_RO)
    def nine_providers() -> dict[str, Any]:
        """Provider health, no secrets."""
        return _self.providers()

    @mcp.tool(title="9router usage", annotations=_RO)
    def nine_usage() -> dict[str, Any]:
        """Requests, tokens, cost, provider list."""
        return _self.usage()

    @mcp.tool(title="9router chat", annotations=_WR)
    def nine_chat(model: str, prompt: str, system: str = "", max_tokens: int = 1024,
                  temperature: float = 0.7, timeout_seconds: int = 120) -> dict[str, Any]:
        """Ask any combo/model. Single-shot completion with usage."""
        return _self.chat(model, prompt, system, max_tokens, temperature, timeout_seconds)

    @mcp.tool(title="9router stream chat", annotations=_WR)
    def nine_chat_stream(model: str, prompt: str, system: str = "",
                         max_tokens: int = 1024,
                         timeout_seconds: int = 120) -> dict[str, Any]:
        """SSE chat; returns concatenated text."""
        return _self.chat_stream(model, prompt, system, max_tokens, timeout_seconds)

    @mcp.tool(title="9router fanout", annotations=_WR)
    def nine_fanout(models: list[str], prompt: str, system: str = "",
                    max_tokens: int = 512,
                    timeout_seconds: int = 180) -> dict[str, Any]:
        """Same prompt to N models in parallel (max 6). Judge/ensemble primitive."""
        return _self.fanout(models, prompt, system, max_tokens, timeout_seconds)

    @mcp.tool(title="9router image", annotations=_WR)
    def nine_image(prompt: str, model: str = "", n: int = 1,
                   size: str = "1024x1024",
                   timeout_seconds: int = 120) -> dict[str, Any]:
        """Generate images (b64_json). Default auto-picks FastImg member."""
        return _self.image(prompt, model, n, size, timeout_seconds)

    @mcp.tool(title="9router TTS", annotations=_WR)
    def nine_tts(text: str, voice: str = "alloy", model: str = "",
                 timeout_seconds: int = 120) -> dict[str, Any]:
        """Text-to-speech. Shape varies by host."""
        return _self.tts(text, voice, model, timeout_seconds)

    @mcp.tool(title="9router STT", annotations=_WR)
    def nine_stt(audio_b64: str, model: str = "",
                 timeout_seconds: int = 180) -> dict[str, Any]:
        """Speech-to-text from base64 audio."""
        return _self.stt(audio_b64, model, timeout_seconds)

    @mcp.tool(title="9router embeddings", annotations=_WR)
    def nine_embeddings(texts: list[str], model: str = "") -> dict[str, Any]:
        """Embed texts. Shape varies by host."""
        return _self.embeddings(texts, model)

    @mcp.tool(title="9router web search", annotations=_RONET)
    def nine_search(query: str, provider: str = "searchapi",
                    count: int = 5) -> dict[str, Any]:
        """Web search through 9router's connection. Keyless for you."""
        return _self.nine_search(query, provider, count)

    @mcp.tool(title="9router video", annotations=_WR)
    def nine_video(prompt: str, model: str = "",
                   timeout_seconds: int = 300) -> dict[str, Any]:
        """Video generation. Speculative on this host; kept for others."""
        return _self.video(prompt, model, timeout_seconds)

    @mcp.resource("nine://status", name="nine-status",
                  title="9router status", description="Gateway health + version.",
                  mime_type="application/json")
    def _nine_status() -> str:
        import json as _json

        return _json.dumps(_self.status(), ensure_ascii=False, indent=1, default=str)

    @mcp.resource("nine://models", name="nine-models",
                  title="9router models", description="Routable combos + models.",
                  mime_type="application/json")
    def _nine_models() -> str:
        import json as _json

        return _json.dumps(_self.models(), ensure_ascii=False, indent=1, default=str)
