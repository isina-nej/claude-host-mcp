"""Mind suite for claude-host-mcp: time, persistent memory, sequential thinking.

Mirrors official servers without new dependencies:
  time                 stdlib zoneinfo (official time server behavior)
  memory               JSON knowledge-graph file (official memory server behavior)
  sequential-thinking  session thought chain (official sequential-thinking behavior)

Memory file: HOST_MCP_MEMORY_FILE, default
~/.local/share/claude-host-mcp/memory.json. Atomic writes (tmp+replace),
thread-locked. Audit logs entity names only, never observation contents.

Thought chain is session-scoped (in-memory, capped 200). Restart clears it;
memory.json persists.

ponytail: memory recall is substring match. Upgrade path: sqlite FTS5
index when graphs exceed ~10k observations.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from . import policy as _policy

_MEM_FILE = pathlib.Path(os.environ.get(
    "HOST_MCP_MEMORY_FILE",
    str(pathlib.Path.home() / ".local/share/claude-host-mcp/memory.json")))
_mem_lock = threading.Lock()
_think_lock = threading.Lock()
_chain: list[dict[str, Any]] = []
_CHAIN_CAP = 200


# ---------- time ----------

def _tz(name: str):
    name = (name or "").strip()
    if not name or name.lower() == "local":
        return datetime.now().astimezone().tzinfo, "local"
    try:
        return ZoneInfo(name), name
    except ZoneInfoNotFoundError:
        return None, ""


def time_now(timezone: str = "local") -> dict[str, Any]:
    """Current time in an IANA timezone (or local)."""
    tz, label = _tz(timezone)
    if tz is None:
        return {"ok": False, "error": f"Unknown timezone: {timezone!r}. Example: Asia/Tehran, UTC."}
    now = datetime.now(tz)
    return {"ok": True, "iso": now.isoformat(), "timezone": label,
            "utc_offset": now.strftime("%z"), "unix": int(now.timestamp())}


def time_convert(moment: str, from_tz: str, to_tz: str) -> dict[str, Any]:
    """Convert ISO datetime between IANA timezones."""
    z1, l1 = _tz(from_tz)
    z2, l2 = _tz(to_tz)
    if z1 is None:
        return {"ok": False, "error": f"Unknown from_tz: {from_tz!r}."}
    if z2 is None:
        return {"ok": False, "error": f"Unknown to_tz: {to_tz!r}."}
    try:
        dt = datetime.fromisoformat(moment.strip())
    except ValueError:
        return {"ok": False, "error": f"Invalid ISO datetime: {moment!r}. Example: 2026-09-07T12:00:00."}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=z1)
    out = dt.astimezone(z2)
    return {"ok": True, "input": dt.isoformat(), "output": out.isoformat(),
            "from": l1, "to": l2}


def time_zones(query: str = "", max_results: int = 20) -> dict[str, Any]:
    """List IANA zones, optionally filtered by substring."""
    q = query.strip().lower()
    zones = sorted(available_timezones())
    if q:
        zones = [z for z in zones if q in z.lower()]
    limit = max(1, min(int(max_results), 200))
    return {"ok": True, "count": len(zones), "zones": zones[:limit]}


# ---------- memory graph ----------

def _load() -> dict[str, Any]:
    try:
        data = json.loads(_MEM_FILE.read_text())
        if isinstance(data, dict):
            data.setdefault("entities", {})
            data.setdefault("relations", [])
            return data
    except (OSError, ValueError):
        pass
    return {"entities": {}, "relations": []}


def _save(data: dict[str, Any]) -> None:
    _MEM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _MEM_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(_MEM_FILE)


def memory_store(entity: str, observation: str, entity_type: str = "general") -> dict[str, Any]:
    """Store one observation on an entity (creates it)."""
    entity, observation = entity.strip(), observation.strip()
    if not entity or not observation:
        return {"ok": False, "error": "entity and observation must not be empty."}
    if len(observation) > 5000:
        return {"ok": False, "error": "observation capped at 5000 chars."}
    with _mem_lock:
        data = _load()
        ent = data["entities"].setdefault(entity, {"type": entity_type[:80], "observations": []})
        ent["observations"].append(observation)
        _save(data)
    _policy.audit("memory_store", {"entity": entity[:120]}, True)
    return {"ok": True, "entity": entity, "observations": len(ent["observations"])}


def memory_link(from_entity: str, to_entity: str, relation: str) -> dict[str, Any]:
    """Link two entities with a typed relation."""
    a, b, r = from_entity.strip(), to_entity.strip(), relation.strip()
    if not a or not b or not r:
        return {"ok": False, "error": "from_entity, to_entity and relation required."}
    with _mem_lock:
        data = _load()
        for e in (a, b):
            data["entities"].setdefault(e, {"type": "general", "observations": []})
        rel = {"from": a, "to": b, "relation": r[:120]}
        if rel not in data["relations"]:
            data["relations"].append(rel)
        _save(data)
    _policy.audit("memory_link", {"from": a[:80], "to": b[:80]}, True)
    return {"ok": True, **rel}


def memory_recall(query: str, max_results: int = 10) -> dict[str, Any]:
    """Substring recall over entities, observations and relations."""
    q = query.strip().lower()
    if not q:
        return {"ok": False, "error": "Empty query."}
    limit = max(1, min(int(max_results), 50))
    with _mem_lock:
        data = _load()
    hits: list[dict[str, Any]] = []
    for name, ent in data["entities"].items():
        obs = [o for o in ent.get("observations", []) if q in o.lower()]
        if q in name.lower() or obs:
            hits.append({"entity": name, "type": ent.get("type"),
                         "observations": obs[:5] if obs else []})
        if len(hits) >= limit:
            break
    rels = [r for r in data["relations"]
            if q in r["from"].lower() or q in r["to"].lower() or q in r["relation"].lower()][:limit]
    return {"ok": True, "entities": hits, "relations": rels,
            "totals": {"entities": len(data["entities"]), "relations": len(data["relations"])}}


def memory_forget(entity: str, observation: str = "") -> dict[str, Any]:
    """Delete an observation (or whole entity + its relations). Prompts."""
    entity = entity.strip()
    if not entity:
        return {"ok": False, "error": "Empty entity."}
    with _mem_lock:
        data = _load()
        if entity not in data["entities"]:
            return {"ok": False, "error": f"Unknown entity: {entity}"}
        if observation.strip():
            obs = data["entities"][entity]["observations"]
            before = len(obs)
            obs[:] = [o for o in obs if o != observation.strip()]
            removed_obs = before - len(obs)
            removed_ent = False
        else:
            del data["entities"][entity]
            data["relations"] = [r for r in data["relations"]
                                 if r["from"] != entity and r["to"] != entity]
            removed_obs, removed_ent = 0, True
        _save(data)
    _policy.audit("memory_forget", {"entity": entity[:120]}, True)
    return {"ok": True, "entity": entity, "removed_entity": removed_ent,
            "removed_observations": removed_obs}


# ---------- sequential thinking ----------

def think(thought: str, thought_number: int = 0, total_thoughts: int = 0,
          next_needed: bool = True) -> dict[str, Any]:
    """Record one reasoning step; returns chain position and guidance."""
    thought = thought.strip()
    if not thought:
        return {"ok": False, "error": "Empty thought."}
    if len(thought) > 8000:
        return {"ok": False, "error": "thought capped at 8000 chars."}
    with _think_lock:
        if len(_chain) >= _CHAIN_CAP:
            _chain.pop(0)
        _chain.append({"n": thought_number or len(_chain) + 1,
                       "total": total_thoughts, "thought": thought})
        depth = len(_chain)
    if next_needed:
        hint = (f"Step {depth} recorded." +
                (f" Continue toward step {total_thoughts}." if total_thoughts else
                 " Continue decomposing; revise earlier steps if assumptions changed."))
    else:
        hint = f"Chain closed at step {depth}. Summarize the conclusion."
    return {"ok": True, "step": depth, "next_needed": next_needed, "guidance": hint}


def think_list() -> dict[str, Any]:
    """Return the current thought chain."""
    with _think_lock:
        return {"ok": True, "steps": len(_chain), "chain": list(_chain)}


def think_clear() -> dict[str, Any]:
    """Clear the thought chain. Prompts."""
    with _think_lock:
        n = len(_chain)
        _chain.clear()
    _policy.audit("think_clear", {"cleared_steps": n}, True)
    return {"ok": True, "cleared_steps": n}


def register(mcp) -> None:
    """Register mind tools. Follows repo wrapper convention (no impl shadowing)."""
    import sys as _sys

    _self = _sys.modules[__name__]
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _WR = _TA(read_only_hint=False, destructive_hint=False,
              idempotent_hint=False, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=False, open_world_hint=False)

    @mcp.tool(title="Current time", annotations=_RO)
    def time_now(timezone: str = "local") -> dict[str, Any]:
        """Current time in an IANA timezone (default local)."""
        return _self.time_now(timezone)

    @mcp.tool(title="Convert time", annotations=_RO)
    def time_convert(moment: str, from_tz: str, to_tz: str) -> dict[str, Any]:
        """Convert ISO datetime between IANA timezones."""
        return _self.time_convert(moment, from_tz, to_tz)

    @mcp.tool(title="List timezones", annotations=_RO)
    def time_zones(query: str = "", max_results: int = 20) -> dict[str, Any]:
        """List IANA zones, optionally filtered."""
        return _self.time_zones(query, max_results)

    @mcp.tool(title="Memory store", annotations=_WR)
    def memory_store(entity: str, observation: str,
                     entity_type: str = "general") -> dict[str, Any]:
        """Store one observation on an entity (persistent knowledge graph)."""
        return _self.memory_store(entity, observation, entity_type)

    @mcp.tool(title="Memory link", annotations=_WR)
    def memory_link(from_entity: str, to_entity: str,
                    relation: str) -> dict[str, Any]:
        """Link two entities with a typed relation."""
        return _self.memory_link(from_entity, to_entity, relation)

    @mcp.tool(title="Memory recall", annotations=_RO)
    def memory_recall(query: str, max_results: int = 10) -> dict[str, Any]:
        """Substring recall over entities, observations and relations."""
        return _self.memory_recall(query, max_results)

    @mcp.tool(title="Memory forget", annotations=_MUT)
    def memory_forget(entity: str, observation: str = "") -> dict[str, Any]:
        """Delete an observation or whole entity. Prompts."""
        return _self.memory_forget(entity, observation)

    @mcp.tool(title="Think step", annotations=_WR)
    def think(thought: str, thought_number: int = 0, total_thoughts: int = 0,
              next_needed: bool = True) -> dict[str, Any]:
        """Record one reasoning step in a sequential chain."""
        return _self.think(thought, thought_number, total_thoughts, next_needed)

    @mcp.tool(title="Think chain", annotations=_RO)
    def think_list() -> dict[str, Any]:
        """Return the current thought chain."""
        return _self.think_list()

    @mcp.tool(title="Think clear", annotations=_MUT)
    def think_clear() -> dict[str, Any]:
        """Clear the thought chain. Prompts."""
        return _self.think_clear()
