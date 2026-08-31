"""Tool registry with permission classes + audit ledger.

Every tool returns a structured dict:
    {"ok": bool, "speech": str, "data": {...}}
The orchestrator speaks `speech`, never raw output.
"""
import json
import re
import threading
import time
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


_RETRYABLE = {"web_search", "deep_research", "browser_navigate", "mail_check"}
_RETRY_COUNT = 2
_RETRY_DELAY = 1.0  # seconds
_CIRCUIT_BREAKER: dict[str, float] = {}  # tool -> timeout expiry timestamp
_CONSECUTIVE_FAILS: dict[str, int] = {}  # tool -> fail count
_BREAKER_THRESHOLD = 5  # failures before tripping
_BREAKER_DURATION = 300.0  # 5 minutes

ERROR_CLASSIFIERS = {
    "permission": ["permission", "denied", "access", "unauthorized", "403", "401", "forbidden"],
    "rate_limit": ["rate limit", "429", "quota", "throttle", "too many requests"],
    "not_found": ["not found", "404", "missing", "no such", "does not exist"],
    "invalid": ["invalid", "bad request", "400", "malformed", "syntax"],
    "network": ["timeout", "connection", "unreachable", "dns", "socket", "econnrefused", "ssl"],
}


def classify_error(error_str: str) -> str:
    lower = str(error_str).lower()
    for category, patterns in ERROR_CLASSIFIERS.items():
        if any(p in lower for p in patterns):
            return category
    return "unknown"


def _format_error_speech(name: str, error_type: str, raw_error: str) -> str:
    descs = {
        "network": "Network error — the remote service or connection timed out.",
        "permission": "Permission denied — access is restricted.",
        "not_found": "Resource not found — the requested item does not exist.",
        "rate_limit": "Rate limited — API request threshold reached.",
        "invalid": "Invalid arguments provided for tool execution.",
        "unknown": f"Execution error: {raw_error[:150]}",
    }
    return descs.get(error_type, f"{name} execution failed.")


def call(name: str, args: dict | None = None) -> dict:
    ensure_loaded()
    t = _REGISTRY.get(name)
    if not t:
        db.audit(name, "{}", False, "unknown tool")
        return {"ok": False, "speech": f"No such capability '{name}'.", "data": {}}
    args = args or {}

    # Circuit breaker check
    now = time.time()
    if _CIRCUIT_BREAKER.get(name, 0) > now:
        remaining = int(_CIRCUIT_BREAKER[name] - now)
        return {
            "ok": False,
            "speech": f"{name} is temporarily disabled for {remaining}s due to consecutive failures.",
            "data": {"circuit_open": True},
        }

    try:
        from ..consent import get_consent_manager
        cm = get_consent_manager()
        if not cm.has_consent(name):
            db.audit(name, str(args)[:200], False, f"consent denied for {name}")
            return {"ok": False, "speech": f"Consent denied: cannot execute '{name}'.", "data": {}}
        from ..autonomy import is_allowed
        if not is_allowed(name, t.permission):
            db.audit(name, _masked_args(args), False, f"permission denied for {name} ({t.permission})")
            return {"ok": False, "speech": f"permission denied: cannot execute '{name}'.", "data": {}}
    except ImportError:
        pass

    from ..kill_switch import get_kill_switch
    if getattr(get_kill_switch(), "is_active", lambda: False)():
        return {"ok": False, "speech": "Kill switch active — all tools disabled.", "data": {}}

    retries = _RETRY_COUNT if name in _RETRYABLE else 0
    last_exc = None

    for attempt in range(1 + retries):
        try:
            result = t.fn(**args)
            if not isinstance(result, dict) or "ok" not in result or "speech" not in result:
                result = {"ok": True, "speech": str(result)[:300], "data": {}}

            if result.get("ok", True):
                _CONSECUTIVE_FAILS[name] = 0
                _CIRCUIT_BREAKER.pop(name, None)
            else:
                _CONSECUTIVE_FAILS[name] = _CONSECUTIVE_FAILS.get(name, 0) + 1
                if _CONSECUTIVE_FAILS[name] >= _BREAKER_THRESHOLD:
                    _CIRCUIT_BREAKER[name] = time.time() + _BREAKER_DURATION

            db.audit(name, _masked_args(args),
                     result.get("ok", True), result.get("speech", ""))
            return result
        except PermissionDenied as exc:
            db.audit(name, json.dumps(args, default=str), False, f"denied: {exc}")
            return {"ok": False, "speech": f"That needs permission I don't have: {exc}", "data": {}}
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(_RETRY_DELAY * (2 ** attempt))
                continue

            _CONSECUTIVE_FAILS[name] = _CONSECUTIVE_FAILS.get(name, 0) + 1
            if _CONSECUTIVE_FAILS[name] >= _BREAKER_THRESHOLD:
                _CIRCUIT_BREAKER[name] = time.time() + _BREAKER_DURATION

            db.audit(name, json.dumps(args, default=str), False, str(exc))
            try:
                from .. import errlog
                errlog.log_error(f"tool:{name}", str(exc))
            except Exception:
                pass

            err_type = classify_error(str(exc))
            speech_msg = _format_error_speech(name, err_type, str(exc))
            return {
                "ok": False,
                "speech": speech_msg,
                "data": {"error_type": err_type, "raw_error": str(exc)[:200]},
            }

    err_type = classify_error(str(last_exc or "Unknown error"))
    return {
        "ok": False,
        "speech": _format_error_speech(name, err_type, str(last_exc or "")),
        "data": {"error_type": err_type},
    }


_loaded = False


def ensure_loaded() -> int:
    """Idempotent builtin-tool registration. Called automatically by call()."""
    global _loaded
    if _loaded:
        return len(manifest())
    from . import system_tools, web_tools, docs_tools, connectors, browser_tools, desktop_tools  # noqa: F401
    from .. import autonomy, calendar_tools, habits, jobs, life_admin, mail_tools, research_tools, skills, vault_secrets, vault, workflows, work_tools, youtube_tools  # noqa: F401
    from .. import coder, initiative_engine, persona_loader, push_notify, security, selfcheck, style_controller  # noqa: F401
    from .. import deep_memory, ensemble, rag  # noqa: F401  (phase 4 tools + watcher)
    from .. import voice_tools, tool_synthesizer, swarm  # noqa: F401  (voice selection + dynamic tools + swarm)

    _loaded = True
    from .. import skills as _skills
    from . import connectors as _conn

    n_skills = _skills.load_all()
    n_conn = _conn.load_all()
    tool_synthesizer.load_all_dynamic_tools()
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
