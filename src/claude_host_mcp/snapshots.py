"""Snapshots and rollback for claude-host-mcp.

snapshot_create copies a file or directory into a timestamped slot under
~/.local/share/claude-host-mcp/snapshots/ (override HOST_MCP_SNAPSHOT_DIR).
snapshot_restore copies it back (destructive: prompts). Slots capped at
50, oldest evicted. file_version is a one-call snapshot of a single file
before editing; file_restore brings back the newest slot for a path.

ponytail: full copies, no binary diffs. Upgrade path: content-addressed
store (sha256 chunks) when repos get large; for now cap slot size at
200MB to avoid disk bombs.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import time
from typing import Any

from . import policy as _policy

_DIR = pathlib.Path(os.environ.get(
    "HOST_MCP_SNAPSHOT_DIR",
    str(pathlib.Path.home() / ".local/share/claude-host-mcp/snapshots")))
_MAX_SLOTS = 50
_MAX_BYTES = 200_000_000


def _du(path: pathlib.Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _d, names in os.walk(path):
        for n in names:
            try:
                total += (pathlib.Path(root) / n).stat().st_size
            except OSError:
                continue
            if total > _MAX_BYTES:
                return total
    return total


def _prune() -> None:
    try:
        slots = sorted(_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    while len(slots) > _MAX_SLOTS:
        oldest = slots.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)


def create(path: str, label: str = "") -> dict[str, Any]:
    from .server import READ_ROOTS

    target, err = _policy.resolve_under(path, READ_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if not target.exists():
        return {"ok": False, "error": f"Path does not exist: {target}"}
    if _du(target) > _MAX_BYTES:
        return {"ok": False, "error": f"Snapshot source exceeds {_MAX_BYTES} bytes."}
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in target.name)[:80]
    slot = _DIR / f"{stamp}-{safe}"
    i = 1
    while slot.exists():
        i += 1
        slot = _DIR / f"{stamp}-{safe}-{i}"
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        if target.is_dir() and not target.is_symlink():
            shutil.copytree(target, slot, symlinks=True)
        else:
            shutil.copy2(target, slot)
        (slot.parent / f"{slot.name}.meta").write_text(
            f"source={target}\nlabel={label[:200]}\n")
        _prune()
        _policy.audit("snapshot_create", {"source": str(target), "slot": slot.name}, True)
        return {"ok": True, "slot": slot.name, "source": str(target), "label": label}
    except Exception as exc:
        _policy.audit("snapshot_create", {"source": str(target), "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


def list_slots() -> list[dict[str, Any]]:
    out = []
    try:
        for slot in sorted(_DIR.iterdir()):
            if slot.name.endswith(".meta"):
                continue
            try:
                st = slot.stat()
                meta = (_DIR / f"{slot.name}.meta").read_text() if \
                    (_DIR / f"{slot.name}.meta").exists() else ""
                out.append({"slot": slot.name,
                            "created": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                     time.localtime(st.st_mtime)),
                            "meta": meta.strip()[:300]})
            except OSError:
                continue
    except OSError:
        pass
    return out


def restore(slot: str, dest: str = "", overwrite: bool = False) -> dict[str, Any]:
    from .server import _gate, WRITE_ROOTS

    if (blocked := _gate("snapshot_restore", "files")) is not None:
        return blocked
    if "/" in slot or slot.startswith("."):
        return {"ok": False, "error": f"Invalid slot: {slot!r}"}
    src = _DIR / slot
    if not src.exists():
        return {"ok": False, "error": f"Unknown slot: {slot}"}
    meta_path = _DIR / f"{slot}.meta"
    original = ""
    if meta_path.exists():
        for line in meta_path.read_text().splitlines():
            if line.startswith("source="):
                original = line.split("=", 1)[1]
    raw_dest = dest or original
    if not raw_dest:
        return {"ok": False, "error": "No dest given and slot has no recorded source."}
    target, err = _policy.resolve_under(raw_dest, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    if target.exists() and not overwrite:
        return {"ok": False, "error": "Destination exists; set overwrite=true to replace it."}
    try:
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, target, symlinks=True)
        else:
            shutil.copy2(src, target)
        _policy.audit("snapshot_restore", {"slot": slot, "dest": str(target)}, True)
        return {"ok": True, "slot": slot, "restored_to": str(target)}
    except Exception as exc:
        _policy.audit("snapshot_restore", {"slot": slot, "error": str(exc)}, False)
        return {"ok": False, "error": str(exc)}


def file_version(path: str) -> dict[str, Any]:
    """One-call pre-edit snapshot of a single file."""
    return create(path, label="file_version")


def file_restore(path: str) -> dict[str, Any]:
    """Restore the newest slot recorded for this exact source path."""
    from .server import WRITE_ROOTS

    target, err = _policy.resolve_under(path, WRITE_ROOTS)
    if err:
        return {"ok": False, "error": err}
    assert target is not None
    best: str | None = None
    best_mtime = -1.0
    try:
        for slot in _DIR.iterdir():
            if slot.name.endswith(".meta"):
                continue
            meta = _DIR / f"{slot.name}.meta"
            if not meta.exists():
                continue
            for line in meta.read_text().splitlines():
                if line == f"source={target}":
                    mtime = slot.stat().st_mtime
                    if mtime > best_mtime:
                        best, best_mtime = slot.name, mtime
    except OSError:
        pass
    if not best:
        return {"ok": False, "error": f"No snapshot recorded for {target}."}
    return restore(best, str(target), overwrite=True)


def register(mcp) -> None:
    from mcp.types import ToolAnnotations as _TA

    _RO = _TA(read_only_hint=True, open_world_hint=False)
    _MUT = _TA(read_only_hint=False, destructive_hint=True,
               idempotent_hint=True, open_world_hint=False)

    @mcp.tool(title="Create snapshot", annotations=_RO)
    def snapshot_create(path: str, label: str = "") -> dict[str, Any]:
        """Copy a file/dir into a timestamped slot before risky ops."""
        return create(path, label)

    @mcp.tool(title="List snapshots", annotations=_RO)
    def snapshot_list() -> list[dict[str, Any]]:
        """List snapshot slots with source and creation time."""
        return list_slots()

    @mcp.tool(title="Restore snapshot", annotations=_MUT)
    def snapshot_restore(slot: str, dest: str = "",
                         overwrite: bool = False) -> dict[str, Any]:
        """Copy a slot back. Prompts (destructive); overwrite required on clash."""
        return restore(slot, dest, overwrite)

    @mcp.tool(title="Version file", annotations=_RO)
    def file_version(path: str) -> dict[str, Any]:
        """One-call pre-edit snapshot of a single file."""
        return file_version(path)

    @mcp.tool(title="Restore file version", annotations=_MUT)
    def file_restore(path: str) -> dict[str, Any]:
        """Restore the newest snapshot recorded for this path."""
        return file_restore(path)
