"""Phase 5: API connector framework — any REST API becomes a tool.

connector_add takes a JSON spec:

    {"name": "weather",
     "description": "Current weather for a city",
     "base_url": "https://api.example.com",
     "method": "GET",                       # GET | POST
     "path": "/v1/weather",                 # {arg} slots get substituted
     "auth_env": "MY_API_TOKEN",            # optional: Bearer token from .env
     "args": {"city": {"in": "query", "required": true,
                        "type": "string"}}}

-> saves data/connectors/weather.json and registers a live `api_weather`
tool immediately (and at every boot). Responses are JSON or text, hard
timeout 15s, every call audited.
"""
import json
import os
import re
from pathlib import Path

from .. import db
from ..config import DATA

CONNECTORS_DIR = DATA / "connectors"
_lock = __import__("threading").Lock()


def _spec_path(name: str) -> Path:
    clean = re.sub(r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_"))[:40]
    return CONNECTORS_DIR / f"{clean}.json"


def validate_spec(spec: dict) -> tuple[bool, str]:
    if not isinstance(spec, dict):
        return False, "spec must be a JSON object"
    name = str(spec.get("name", ""))
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,30}", name.strip().lower()
                        .replace(" ", "_").replace("-", "_")):
        return False, ("'name' must be 3-31 chars: letters/digits/underscore")
    if not str(spec.get("base_url", "")).startswith(("http://", "https://")):
        return False, "'base_url' must be an http(s) URL"
    method = str(spec.get("method", "GET")).upper()
    if method not in ("GET", "POST"):
        return False, "'method' must be GET or POST"
    for a, cfg in (spec.get("args") or {}).items():
        if not isinstance(cfg, dict) or cfg.get("in") not in ("query", "path",
                                                              "body"):
            return False, f"arg '{a}' needs in: query|path|body"
    return True, ""


def register(spec: dict) -> None:
    """Attach a live tool to the registry from a saved spec."""
    from . import Tool, _REGISTRY

    name = "api_" + re.sub(r"[^a-z0-9_]", "", str(spec["name"]).lower()
                           .replace(" ", "_").replace("-", "_"))

    def invoke(**kwargs) -> dict:
        return _call_spec(spec, kwargs)

    t = Tool(name=name,
             description=str(spec.get("description") or spec["name"])[:280],
             args_schema={k: {"type": cfg.get("type", "string")}
                          for k, cfg in (spec.get("args") or {}).items()},
             permission="comms" if spec.get("auth_env") else "read",
             fn=invoke)
    with _lock:
        _REGISTRY[t.name] = t


def _call_spec(spec: dict, kwargs: dict) -> dict:
    import urllib.parse
    import urllib.request

    timeout = 15
    path = str(spec.get("path", "/"))
    query: dict[str, str] = {}
    body: dict = {}
    headers = {"User-Agent": "EVO-MK2"}
    for key, cfg in (spec.get("args") or {}).items():
        if key in kwargs and kwargs[key] not in (None, ""):
            v = kwargs[key]
            where = cfg.get("in", "query")
            if where == "query":
                query[key] = str(v)
            elif where == "body":
                body[key] = v
            else:
                path = path.replace("{" + key + "}", urllib.parse.quote(str(v), safe=""))
    auth_env = spec.get("auth_env")
    if auth_env:
        try:
            from ..vault_secrets import vault_lookup

            token = vault_lookup(auth_env)
        except Exception:
            token = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
    url = spec["base_url"].rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=spec.get("method", "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = resp.status
    except Exception as exc:
        return {"ok": False, "speech": f"{spec['name']} unreachable: "
                                       f"{str(exc)[:150]}", "data": {}}
    try:
        payload = json.loads(raw)
    except Exception:
        payload = raw[:2000]
    summary = json.dumps(payload, ensure_ascii=False)[:400]
    return {"ok": True, "speech": summary,
            "data": {"status": status, "response": payload}}


def load_all() -> int:
    """Register all saved connectors (boot + connector_add)."""
    if not CONNECTORS_DIR.exists():
        return 0
    n = 0
    for p in sorted(CONNECTORS_DIR.glob("*.json")):
        try:
            spec = json.loads(p.read_text(encoding="utf-8"))
            ok, _msg = validate_spec(spec)
            if ok:
                register(spec)
                n += 1
        except Exception:
            continue
    return n


# ------------------------------------------------------------------ tools

from . import tool  # noqa: E402


@tool("connector_add", "Teach EVO a new REST API instantly. Provide a JSON spec with name, base_url, path, method, optional auth_env, and args (each with in: query|path|body).",
      {"spec_json": {"type": "string"}}, permission="execute")
def connector_add(spec_json: str) -> dict:
    try:
        spec = json.loads(spec_json)
    except Exception as exc:
        return {"ok": False, "speech": f"Bad JSON: {exc}", "data": {}}
    ok, msg = validate_spec(spec)
    if not ok:
        return {"ok": False, "speech": msg, "data": {}}
    CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
    _spec_path(spec["name"]).write_text(json.dumps(spec, indent=1),
                                        encoding="utf-8")
    register(spec)
    db.audit("connector_add", spec["name"], True, spec.get("base_url", ""))
    return {"ok": True,
            "speech": (f"Connected. New tool 'api_{str(spec['name']).replace(' ', '_').lower()}' "
                       f"is live - try it now."),
            "data": {"tool": "api_" + str(spec["name"]).replace(" ", "_").lower()}}


@tool("connector_list", "List installed API connectors.", {}, permission="read")
def connector_list() -> dict:
    if not CONNECTORS_DIR.exists():
        return {"ok": True, "speech": "No connectors yet.", "data": {"connectors": []}}
    items = []
    for p in sorted(CONNECTORS_DIR.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            items.append({"name": s.get("name"), "base_url": s.get("base_url"),
                          "description": s.get("description", "")})
        except Exception:
            continue
    speech = ", ".join(i["name"] or "?" for i in items) or "none"
    return {"ok": True, "speech": f"Connectors: {speech}",
            "data": {"connectors": items}}


@tool("connector_delete", "Remove a connector by name.",
      {"name": {"type": "string"}}, permission="execute")
def connector_delete(name: str) -> dict:
    p = _spec_path(name)
    existed = p.exists()
    if existed:
        p.unlink()
        from . import _REGISTRY

        _REGISTRY.pop("api_" + re.sub(r"[^a-z0-9_]", "",
                                      name.lower().replace(" ", "_")), None)
    return {"ok": existed,
            "speech": "Removed." if existed else f"No connector '{name}'.",
            "data": {}}
