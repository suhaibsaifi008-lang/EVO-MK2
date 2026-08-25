"""Tool registry with permission classes + audit ledger.

Every tool returns a structured dict:
    {"ok": bool, "speech": str, "data": {...}}
The orchestrator speaks `speech`, never raw output.
"""
import json
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from .. import db

_lock = threading.Lock()
_REGISTRY: dict[str, "Tool"] = {}
_emitter = {"fn": None}


def set_emitter(fn) -> None:
    """Brain attaches its event emitter so long tools can stream progress."""
    _emitter["fn"] = fn


def emit_progress(text: str) -> None:
    fn = _emitter.get("fn")
    if fn:
        try:
            fn({"type": "progress", "text": str(text)[:160]})
        except Exception:
            pass


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
            {"name": t.name, "description": t.description, "args": t.args_schema,
             "long_running": t.long_running}
            for t in _REGISTRY.values()
        ]


_SENSITIVE = ("value", "password", "token", "secret", "api_key")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _masked_args(args: dict) -> str:
    """Never let secret values or raw emails into the immutable ledger."""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and any(s in k.lower() for s in _SENSITIVE):
            out[k] = "(hidden)"
        else:
            out[k] = v
    blob = json.dumps(out, default=str)
    return _EMAIL_RE.sub(lambda m: m.group(0)[0] + "***"
                         + m.group(0)[m.group(0).find("@"):], blob)


def call(name: str, args: dict | None = None) -> dict:
    ensure_loaded()
    t = _REGISTRY.get(name)
    if not t:
        db.audit(name, "{}", False, "unknown tool")
        return {"ok": False, "speech": f"No such capability '{name}'.", "data": {}}
    args = args or {}
    try:
        result = t.fn(**args)
        if not isinstance(result, dict) or "ok" not in result or "speech" not in result:
            result = {"ok": True, "speech": str(result)[:300], "data": {}}
        db.audit(name, _masked_args(args),
                 result.get("ok", True), result.get("speech", ""))
        return result
    except PermissionDenied as exc:
        db.audit(name, json.dumps(args, default=str), False, f"denied: {exc}")
        return {"ok": False, "speech": f"That needs permission I don't have: {exc}", "data": {}}
    except Exception as exc:
        db.audit(name, json.dumps(args, default=str), False, str(exc))
        try:
            from .. import errlog

            errlog.log_error(f"tool:{name}", str(exc))
        except Exception:
            pass
        return {"ok": False, "speech": f"Failed: {str(exc)[:200]}", "data": {}}


_loaded = False


def ensure_loaded() -> int:
    """Idempotent builtin-tool registration. Called automatically by call()."""
    global _loaded
    if _loaded:
        return len(manifest())
    from . import system_tools, web_tools, docs_tools, connectors, browser_tools  # noqa: F401
    from .. import calendar_tools, habits, jobs, life_admin, mail_tools, research_tools, skills, vault_secrets, vault, workflows, work_tools, youtube_tools  # noqa: F401
    from .. import coder, initiative_engine, persona_loader, push_notify, security, selfcheck, style_controller  # noqa: F401
    from .. import deep_memory, ensemble, rag  # noqa: F401  (phase 4 tools + watcher)
    from .. import voice_tools  # noqa: F401  (voice selection)

    _loaded = True
    from .. import skills as _skills
    from . import connectors as _conn

    n_skills = _skills.load_all()
    n_conn = _conn.load_all()
    return len(manifest())


def load_builtin_tools() -> int:
    return ensure_loaded()


@tool("tool_help", "Show full usage details (description + argument schema) for one tool by name.",
      {"name": {"type": "string"}}, permission="read")
def tool_help(name: str) -> dict:
    t = _REGISTRY.get((name or "").strip())
    if not t:
        return {"ok": False, "speech": f"No tool '{name}'.", "data": {}}
    return {"ok": True,
            "speech": f"{t.name}: {t.description}",
            "data": {"name": t.name, "args_schema": t.args_schema,
                     "permission": t.permission}}
