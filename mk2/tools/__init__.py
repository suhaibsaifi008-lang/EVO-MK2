"""Tool registry with permission classes + audit ledger.

Every tool returns a structured dict:
    {"ok": bool, "speech": str, "data": {...}}
The orchestrator speaks `speech`, never raw output.
"""
import json
import threading
from dataclasses import dataclass, field
from typing import Callable

from .. import db

_lock = threading.Lock()
_REGISTRY: dict[str, "Tool"] = {}


class PermissionDenied(RuntimeError):
    pass


PERMISSIONS = ("read", "execute", "comms")


@dataclass
class Tool:
    name: str
    description: str
    args_schema: dict
    permission: str  # read | execute | comms
    fn: Callable[..., dict]
    long_running: bool = False


def tool(name: str, description: str, args: dict | None = None,
         permission: str = "read", long_running: bool = False):
    def deco(fn):
        with _lock:
            _REGISTRY[name] = Tool(name, description, args or {}, permission, fn, long_running)
        return fn
    return deco


def manifest() -> list[dict]:
    with _lock:
        return [
            {"name": t.name, "description": t.description, "args": t.args_schema}
            for t in _REGISTRY.values()
        ]


def call(name: str, args: dict | None = None) -> dict:
    ensure_loaded()
    t = _REGISTRY.get(name)
    if not t:
        db.audit(name, json.dumps(args or {}, default=str), False, "unknown tool")
        return {"ok": False, "speech": f"No such capability '{name}'.", "data": {}}
    args = args or {}
    try:
        result = t.fn(**args)
        if not isinstance(result, dict) or "ok" not in result or "speech" not in result:
            result = {"ok": True, "speech": str(result)[:300], "data": {}}
        db.audit(name, json.dumps(args, default=str), result.get("ok", True),
                 result.get("speech", ""))
        return result
    except PermissionDenied as exc:
        db.audit(name, json.dumps(args, default=str), False, f"denied: {exc}")
        return {"ok": False, "speech": f"That needs permission I don't have: {exc}", "data": {}}
    except Exception as exc:
        db.audit(name, json.dumps(args, default=str), False, str(exc))
        return {"ok": False, "speech": f"Failed: {str(exc)[:200]}", "data": {}}


_loaded = False


def ensure_loaded() -> int:
    """Idempotent builtin-tool registration. Called automatically by call()."""
    global _loaded
    if _loaded:
        return len(manifest())
    from . import system_tools, web_tools  # noqa: F401  (register side effects)
    from .. import calendar_tools, vault, work_tools  # noqa: F401  (work tools + vault)

    _loaded = True
    return len(manifest())


def load_builtin_tools() -> int:
    return ensure_loaded()
