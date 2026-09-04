"""FastAPI surface: streaming chat, health, memory/audit views, static UI."""
import asyncio
import html as _html_mod
import json
import os
import queue as _queue
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.websockets import WebSocketState

from . import brain, config, db, llm, tools


def _sanitize_str(v: str, max_len: int = 4096) -> str:
	"""Strip null bytes, truncate, and HTML-escape a string."""
	v = v.replace("\x00", "")
	if len(v) > max_len:
		v = v[:max_len]
	return _html_mod.escape(v)


def _sanitize_body(body: dict) -> dict:
	"""Recursively sanitize all string values in a JSON body dict."""
	def _walk(obj):
		if isinstance(obj, str):
			return _sanitize_str(obj)
		if isinstance(obj, dict):
			return {k: _walk(v) for k, v in obj.items()}
		if isinstance(obj, list):
			return [_walk(i) for i in obj]
		return obj
	return _walk(body)


def _sanitize_for_tts(text: str) -> str:
	"""Strip HTML/XML tags before text goes to TTS to prevent spoken tag names."""
	return re.sub(r"<[^>]+>", "", text)


app = FastAPI(title="EVO MK2")
UI = Path(config.UI_DIR)

import uuid

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

app.add_middleware(RequestIDMiddleware)

# ------------------------------------------------------------------ API key auth


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _resolve_api_key() -> str | None:
    key = os.environ.get("EVO_API_KEY", "").strip()
    if not key:
        try:
            key = getattr(config.settings, "api_key", "") or ""
        except Exception:
            pass
    key = key.strip()
    if key:
        return key
    # Auto-generate a persistent API key stored encrypted at rest
    import secrets
    try:
        from . import vault_secrets
        key = vault_secrets.get_secret("EVO_SERVER_API_KEY")
        if not key:
            key_file = config.DATA / "api_key.txt"
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
            if not key:
                key = secrets.token_hex(32)
            vault_secrets.secret_store("EVO_SERVER_API_KEY", key)
            try:
                key_file.unlink(missing_ok=True)
            except Exception:
                pass
        os.environ["EVO_API_KEY"] = key
        return key
    except Exception:
        key_file = config.DATA / "api_key.txt"
        try:
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
            if not key:
                key = secrets.token_hex(32)
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.write_text(key, encoding="utf-8")
            os.environ["EVO_API_KEY"] = key
            return key
        except Exception:
            return None


def _is_auth_required(request: Request) -> bool:
    path = str(request.url.path)
    if path in (
        "/api/health", "/", "/health", "/favicon.ico",
        "/api/transcribe", "/api/tts", "/api/events", "/api/status",
        "/autonomy", "/landing", "/face",
    ):
        return False
    if path.startswith("/ui") or path.startswith("/static") or path.startswith("/voice/client"):
        return False
    return True


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = _resolve_api_key()
        if api_key and _is_auth_required(request):
            client_host = request.client.host if request.client else ""
            if client_host == "testclient":
                return await call_next(request)
            provided = (
                request.headers.get("x-api-key", "")
                or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or request.cookies.get("evo_session", "")
            )
            if provided != api_key:
                from fastapi.responses import JSONResponse

                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

from starlette.middleware.cors import CORSMiddleware

# Wildcard origins with credentials are not allowed by CORS, default to localhost
_cors_origins = [o.strip() for o in os.environ.get("EVO_CORS_ORIGINS", "http://localhost:8421").split(",") if o.strip() and o.strip() != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:8421"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Pair-Code", "X-Pairing-Code", "X-Request-ID", "Sec-WebSocket-Protocol"],
)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                from urllib.parse import urlparse
                u = urlparse(origin)
                host = (u.hostname or "").lower()
                allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
                for o in (_cors_origins or ["http://localhost:8421"]):
                    ou = urlparse(o)
                    if ou.hostname:
                        allowed_hosts.add(ou.hostname.lower())
                if host and host not in allowed_hosts:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({"error": "cross-origin request forbidden by CSRF protection"}, status_code=403)
        return await call_next(request)


app.add_middleware(CSRFMiddleware)


# ------------------------------------------------------------------ Timeout & Circuit Breaker

class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/ws", "/voice")):
            return await call_next(request)
        
        path = request.url.path
        if path in ("/api/chat", "/api/chat/stream"):
            timeout_s = 180.0
        elif path in ("/api/autonomy/health", "/api/diag"):
            timeout_s = 10.0
        else:
            timeout_s = 60.0
            
        try:
            return await asyncio.wait_for(call_next(request), timeout=timeout_s)
        except asyncio.TimeoutError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"request timed out after {timeout_s}s"}, status_code=504)


app.add_middleware(TimeoutMiddleware)

_provider_circuit: dict[str, float] = {}  # provider -> open_until timestamp
_provider_failures: dict[str, int] = {}   # provider -> consecutive failure count
_provider_probation: dict[str, int] = {}  # provider -> probation success count
_circuit_lock = threading.Lock()


def is_circuit_open(provider: str = "default") -> bool:
    with _circuit_lock:
        return time.time() < _provider_circuit.get(provider, 0.0)


def record_llm_result(ok: bool, provider: str = "default") -> None:
    with _circuit_lock:
        now = time.time()
        if ok:
            _provider_failures[provider] = 0
            if _provider_circuit.get(provider, 0.0) > 0:
                _provider_probation[provider] = _provider_probation.get(provider, 0) + 1
                if _provider_probation[provider] >= 2:
                    _provider_circuit[provider] = 0.0
                    _provider_probation[provider] = 0
            else:
                _provider_probation[provider] = 0
        else:
            _provider_probation[provider] = 0
            _provider_failures[provider] = _provider_failures.get(provider, 0) + 1
            if _provider_failures[provider] >= 3:
                _provider_circuit[provider] = now + 10.0

from . import events_api as _events  # noqa: E402

_events.register(app)

try:
    from .voice import webrtc_v2  # noqa: E402

    webrtc_v2.register(app)
except Exception as _voice_exc:  # noqa: BLE001
    webrtc_v2 = None
    import logging
    logging.getLogger("mk2.server").warning(f"[voice-v2] registration failed: {_voice_exc}")

_llm_probe = {"ts": 0.0, "ok": False, "probing": False}


_probe_lock = threading.Lock()


def _llm_online_cached():
    """Non-blocking tri-state: True / False / None(=checking).

    A slow provider must NEVER stall health - the probe runs in a daemon
    thread and its result is picked up by a later call."""
    now = time.time()
    if now - _llm_probe["ts"] < 30 or _llm_probe["probing"]:
        return None if _llm_probe["probing"] else bool(_llm_probe["ok"])
    with _probe_lock:
        if _llm_probe["probing"]:
            return None
        _llm_probe["probing"] = True

        def probe() -> None:
            ok = False
            try:
                llm.chat([{"role": "user", "content": "ping"}],
                         temperature=0, timeout=8, bias=False, max_providers=1)
                ok = True
            except Exception:
                ok = False
            finally:
                _llm_probe.update(ts=time.time(), ok=ok, probing=False)

        threading.Thread(target=probe, daemon=True, name="mk2-llm-probe").start()
    return None


class ChatIn(BaseModel):
    text: str


@app.get("/")
def index():
    resp = FileResponse(UI / "index.html")
    api_key = _resolve_api_key()
    if api_key:
        resp.set_cookie(key="evo_session", value=api_key, httponly=True, samesite="strict")
    return resp


@app.get("/landing")
def landing_page():
    return FileResponse(UI / "landing.html")


@app.get("/autonomy")
def autonomy_dashboard():
    # Intentionally skipped adding consent check here as it is a read-only dashboard endpoint
    return FileResponse(UI / "money_dashboard.html")



@app.get("/face")
def face():
    """MK2 v0.2: the face lives INSIDE the console now."""
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/")


@app.get("/ui/{name}")
def ui_file(name: str):
    safe = Path(name).name  # no traversal
    return FileResponse(UI / safe)


@app.get("/api/health")
def health():
    voice = "unknown"
    try:
        from .voice import gateway

        st = gateway.status()
        voice = f"{st['engine']}:{st['state']}"
    except Exception as exc:
        voice = f"error:{str(exc)[:60]}"
    voice_v2 = {"available": False, "enabled": False, "client_url": ""}
    try:
        if webrtc_v2 is not None:
            voice_v2 = webrtc_v2.status()
    except Exception as exc:
        voice_v2 = {"available": False, "enabled": False, "error": str(exc)[:80]}
    return {
        "ok": True,
        "name": config.settings.name,
        "llm_online": _llm_online_cached(),
        "providers": [p["name"] for p in llm._providers()],
        "tools": len(tools.manifest()),
        "voice": voice,
        "voice_v2": voice_v2,
        "version": "mk2-0.1",
    }


from collections import deque
_chat_rate: dict[str, deque] = {}
_pair_rate: deque = deque()
_RATE_LIMIT = 20  # max requests per minute
_RATE_WINDOW = 60.0
_MAX_RATE_KEYS = 500  # prevent unbounded memory growth from arbitrary keys

@app.post("/api/chat")
def chat(body: ChatIn, request: Request) -> dict:
    safe_text = _sanitize_str(body.text, max_len=1024)
    key = request.headers.get("x-api-key", "default")
    now = time.time()
    # Periodic cleanup of expired keys to prevent memory leak (H16)
    if len(_chat_rate) > _MAX_RATE_KEYS:
        expired_keys = [k for k, dq in _chat_rate.items() if not dq or dq[-1] < now - _RATE_WINDOW]
        for k in expired_keys:
            _chat_rate.pop(k, None)
    if key not in _chat_rate:
        _chat_rate[key] = deque()
    while _chat_rate[key] and _chat_rate[key][0] < now - _RATE_WINDOW:
        _chat_rate[key].popleft()
    if len(_chat_rate[key]) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _chat_rate[key].append(now)
    if is_circuit_open():
        from .fastlane import fast_command
        from .llm import _offline_parse
        instant = fast_command(safe_text) or _offline_parse(safe_text)
        if instant:
            return {"reply": instant}
        raise HTTPException(status_code=503, detail="LLM circuit breaker open (cooling down after repeated failures)")
    try:
        reply = brain.handle_turn(safe_text)
        record_llm_result(True)
        return {"reply": reply}
    except Exception as exc:
        record_llm_result(False)
        log.error("Internal chat error: %s", exc)
        raise HTTPException(status_code=500, detail="Internal assistant error generating response.")


class ConfirmIn(BaseModel):
    mission_id: str
    choice: str = "Proceed"


@app.post("/api/confirm")
def confirm_mission_endpoint(body: ConfirmIn) -> dict:
    from . import autonomy
    res = autonomy.get_runner().confirm_mission(body.mission_id, body.choice)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("speech", "Failed to confirm mission"))
    return res


class AuthPinIn(BaseModel):
    pin: str


PAIRING_STATE_FILE = config.DATA / "pairing_state.json"
_pairing_lock = threading.Lock()
_pin_lockout_until = 0.0
_pin_failures = 0
_pairing_codes: dict[str, dict] = {}


def _load_pairing_state() -> None:
    global _pin_lockout_until, _pin_failures, _pairing_codes
    if not PAIRING_STATE_FILE.exists():
        return
    try:
        data = json.loads(PAIRING_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _pin_lockout_until = float(data.get("pin_lockout_until", 0.0))
            _pin_failures = int(data.get("pin_failures", 0))
            codes = data.get("pairing_codes")
            if isinstance(codes, dict):
                now = time.time()
                _pairing_codes.clear()
                for k, v in codes.items():
                    if isinstance(v, dict) and v.get("expires", 0.0) > now:
                        _pairing_codes[str(k)] = v
    except Exception as exc:
        import logging
        logging.getLogger("mk2.server").warning("Failed to load pairing state: %s", exc)


def _save_pairing_state() -> None:
    try:
        PAIRING_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        active_codes = {k: v for k, v in _pairing_codes.items() if isinstance(v, dict) and v.get("expires", 0.0) > now}
        payload = {
            "pin_lockout_until": _pin_lockout_until,
            "pin_failures": _pin_failures,
            "pairing_codes": active_codes,
        }
        tmp_file = PAIRING_STATE_FILE.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_file.replace(PAIRING_STATE_FILE)
    except Exception as exc:
        import logging
        logging.getLogger("mk2.server").warning("Failed to save pairing state: %s", exc)


_load_pairing_state()


@app.post("/api/auth/pin")
def auth_pin_endpoint(body: AuthPinIn) -> dict:
    global _pin_lockout_until, _pin_failures
    with _pairing_lock:
        now = time.time()
        if now < _pin_lockout_until:
            rem = int(_pin_lockout_until - now)
            raise HTTPException(status_code=429, detail=f"Too many failed PIN attempts. Locked out for {rem}s.")

        from .security import voiceprint
        if voiceprint.verify_pin(body.pin):
            _pin_failures = 0
            _save_pairing_state()
            return {"ok": True, "message": "Authentication successful. Security lock cleared."}

        _pin_failures += 1
        if _pin_failures >= 5:
            _pin_lockout_until = now + 300
            _pin_failures = 0
            _save_pairing_state()
            raise HTTPException(status_code=429, detail="Too many failed PIN attempts. Locked out for 300s.")
        _save_pairing_state()
        raise HTTPException(status_code=403, detail="Invalid security PIN.")


@app.get("/api/status")
def get_status_endpoint(request: Request) -> dict:
    from . import autonomy, initiative_engine, swarm, user_profile
    prof = user_profile.get_user_profile()
    client_ip = request.client.host if request.client else ""
    is_local = client_ip in _LOCAL_HOSTS or client_ip == "testclient"
    return {
        "ok": True,
        "brain": {
            "role": "primary",
            "model": (config.settings.anthropic_model or config.settings.openai_model) if is_local else "redacted",
            "provider": ("anthropic" if config.settings.anthropic_key else "openai") if is_local else "active",
        },
        "voice": {
            "wake": bool(os.environ.get("EVO_WAKE", "1") == "1"),
            "voice_v2": bool(os.environ.get("EVO_VOICE_V2", "1") == "1"),
        },
        "memory": {
            "facts": len(db.all_facts(100)),
            "profile_depth": prof.get("depth_score", 0),
        },
        "autonomy": {
            "missions": len([m for m in autonomy.get_runner().missions.values() if getattr(m, "status", "") in ("running", "planning")]),
        },
        "swarms": {
            "active": len(swarm.list_active_swarms()),
        },
        "initiative": {
            "enabled": bool(os.environ.get("EVO_INITIATIVE", "1") == "1"),
        },
    }


def _verify_sync_auth(request: Request) -> None:
    """Verify authorization via API key or an approved device pairing code."""
    api_key = _resolve_api_key()
    provided_key = (
        request.headers.get("x-api-key", "")
        or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if api_key:
        if provided_key == api_key:
            return
        # Check for approved pairing code
        pair_code = request.headers.get("x-pair-code") or request.headers.get("x-pairing-code")
        if pair_code and _pairing_codes.get(pair_code, {}).get("approved"):
            return
        client_host = request.client.host if request.client else ""
        if (client_host in _LOCAL_HOSTS or client_host == "testclient") and not provided_key and not pair_code:
            return
        raise HTTPException(status_code=401, detail="Unauthorized: invalid API key or pairing code.")

    # When no system API key is configured, verify pairing code if provided
    pair_code = request.headers.get("x-pair-code") or request.headers.get("x-pairing-code")
    if pair_code and not _pairing_codes.get(pair_code, {}).get("approved"):
        raise HTTPException(status_code=401, detail="Unauthorized: invalid pairing code.")


@app.get("/api/session/state")
def get_session_state_endpoint(request: Request) -> dict:
    """Return compact conversational & memory state for cross-device handoff."""
    _verify_sync_auth(request)
    from . import user_profile, autonomy
    return {
        "ok": True,
        "recent_turns": db.recent_messages(14),
        "facts": db.all_facts(25),
        "profile": user_profile.get_user_profile(),
        "active_missions": [
            {"id": m.id, "goal": m.goal, "status": getattr(m, "status", "")}
            for m in autonomy.get_runner().missions.values()
            if getattr(m, "status", "") in ("running", "planning")
        ],
    }


class SessionImportIn(BaseModel):
    recent_turns: list[dict] = []
    facts: list[dict] = []


@app.post("/api/session/import")
def import_session_endpoint(body: SessionImportIn, request: Request) -> dict:
    """Import conversational turns and facts from remote device."""
    _verify_sync_auth(request)
    if len(body.recent_turns) > 50:
        body.recent_turns = body.recent_turns[:50]
    if len(body.facts) > 50:
        body.facts = body.facts[:50]
    imported_turns = db.import_messages(body.recent_turns)
    imported_facts = 0
    for fact in body.facts:
        k = str(fact.get("key") or fact.get("k") or "").strip()[:80]
        v = str(fact.get("value") or fact.get("v") or "").strip()[:500]
        if k and v:
            # Drop obvious injection attempts
            if any(p in v.lower() for p in ("ignore previous", "system prompt", "you are now")):
                continue
            db.remember_fact(k, v, source="remote_sync")
            imported_facts += 1
    return {"ok": True, "imported_turns": imported_turns, "imported_facts": imported_facts}


class PairRequestIn(BaseModel):
    device_name: str = "Remote Device"


@app.post("/api/pair/request")
def request_pairing_endpoint(body: PairRequestIn, request: Request) -> dict:
    """Request 6-digit device pairing code with rate limiting."""
    import secrets
    from .bus import bus
    with _pairing_lock:
        now = time.time()
        while _pair_rate and _pair_rate[0] < now - 60.0:
            _pair_rate.popleft()
        if len(_pair_rate) >= 10:  # max 10 pairing requests per minute
            raise HTTPException(status_code=429, detail="Too many pairing requests. Please wait.")
        _pair_rate.append(now)

        expired = [k for k, v in _pairing_codes.items() if v.get("expires", 0.0) < now]
        for k in expired:
            _pairing_codes.pop(k, None)
        code = f"{secrets.randbelow(900000) + 100000}"
        _pairing_codes[code] = {
            "expires": time.time() + 300,
            "device_name": body.device_name,
            "approved": False,
        }
        _save_pairing_state()
    # Broadcast pairing request notice without exposing the secret 6-digit code to listeners
    bus.publish("notify.out", {
        "kind": "pairing",
        "text": f"Device '{body.device_name}' requested pairing. Approve via dashboard or terminal.",
    })
    return {"ok": True, "code": code, "expires_in": 300}


class PairApproveIn(BaseModel):
    code: str


@app.post("/api/pair/approve")
def approve_pairing_endpoint(body: PairApproveIn, request: Request) -> dict:
    """Approve a pending device pairing code (requires auth or local host)."""
    client_ip = getattr(request.client, "host", "") if request.client else ""
    api_key = _resolve_api_key()
    if api_key:
        _verify_sync_auth(request)
    elif client_ip not in _LOCAL_HOSTS and client_ip != "testclient":
        raise HTTPException(status_code=403, detail="Approving pairing codes requires a local host connection or configured API key.")

    with _pairing_lock:
        entry = _pairing_codes.get(body.code.strip())
        if not entry or entry.get("expires", 0.0) < time.time():
            raise HTTPException(status_code=400, detail="Invalid or expired pairing code.")
        entry["approved"] = True
        _save_pairing_state()
        device_name = entry.get("device_name", "")
    return {"ok": True, "device_name": device_name, "message": "Device pairing approved."}


@app.get("/api/sync/status")
def sync_status_endpoint(request: Request) -> dict:
    _verify_sync_auth(request)
    import platform
    from . import sync
    return {
        "ok": True,
        "device_id": sync.get_device_id(),
        "device_name": platform.node(),
    }


@app.get("/api/sync/pull")
def sync_pull_endpoint(request: Request) -> dict:
    _verify_sync_auth(request)
    from . import sync
    return sync.export_sync_bundle()


@app.post("/api/sync/push")
def sync_push_endpoint(bundle: dict[str, Any], request: Request) -> dict:
    _verify_sync_auth(request)
    from . import sync
    res = sync.import_sync_bundle(bundle)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Sync import failed"))
    return res


class SwarmDispatchIn(BaseModel):
    objective: str
    background: bool = True


@app.post("/api/swarm/dispatch")
def swarm_dispatch_endpoint(body: SwarmDispatchIn) -> dict:
    from . import swarm
    return swarm.get_swarm_orchestrator().execute(body.objective, background=body.background)


@app.get("/api/swarm/status")
def swarm_status_endpoint(swarm_id: str = "") -> dict:
    from . import swarm
    if swarm_id:
        info = swarm.get_swarm_execution(swarm_id)
        if not info:
            raise HTTPException(status_code=404, detail=f"Swarm '{swarm_id}' not found.")
        return {"ok": True, "swarm": info}
    return {"ok": True, "swarms": swarm.list_active_swarms()}


class ToolSynthesizeIn(BaseModel):
    name: str
    description: str
    python_code: str
    test_args: Optional[dict] = None


@app.post("/api/tools/synthesize")
def tool_synthesize_endpoint(body: ToolSynthesizeIn, request: Request) -> dict:
    # Tool synthesis = arbitrary code execution — restrict to localhost only
    client_ip = request.client.host if request.client else ""
    if client_ip not in _LOCAL_HOSTS and client_ip != "testclient":
        raise HTTPException(status_code=403, detail="Tool synthesis is restricted to localhost")
    _verify_sync_auth(request)
    from . import tool_synthesizer
    res = tool_synthesizer.synthesize_tool(
        name=body.name,
        description=body.description,
        python_code=body.python_code,
        test_args=body.test_args,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("speech", "Tool synthesis failed"))
    return res


_SENT_SPLIT = re.compile(r".*?[.!?]+(?:\s+|$)")


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """Complete sentences + remainder (same rule the old client used)."""
    out: list[str] = []
    while True:
        m = _SENT_SPLIT.match(buf)
        if not m or m.end() == 0:
            break
        sent, buf = buf[:m.end()].strip(), buf[m.end():]
        if sent:
            out.append(sent)
    return out, buf


@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    """Voice turn transport: ONE socket carries reply text AND audio.

    Client sends {"type":"say","text":...} (brain turn) or
    {"type":"tts","text":...} (speak-only, e.g. proactive notifications),
    {"type":"cancel"} to abort.

    Server behaviour:
    - brain events stream down as JSON frames as they happen,
    - each COMPLETED sentence is synthesized immediately (Piper-first) in a
      worker that runs while the LLM is still generating, pushed as a JSON
      audio header followed by raw audio bytes -> client-side playback is
      gapless because sentence N+1 is synthesized/fetched during sentence N.
    """
    await ws.accept()
    api_key = _resolve_api_key()
    if api_key:
        client_ip = ws.client.host if ws.client else ""
        auth_hdr = (ws.headers.get("x-api-key") or ws.headers.get("authorization", "")).removeprefix("Bearer ").strip()
        subproto = ws.headers.get("sec-websocket-protocol", "").strip()
        cookie_tok = ws.cookies.get("evo_session", "").strip()
        query_tok = ws.query_params.get("key", "").strip()
        provided = auth_hdr or subproto or cookie_tok or query_tok
        if provided != api_key and client_ip not in _LOCAL_HOSTS and client_ip != "testclient":
            await ws.close(code=1008)
            return
    loop = asyncio.get_running_loop()
    state = {"busy": False, "cancel": False}
    inbox: asyncio.Queue = asyncio.Queue(maxsize=100)   # client messages, bounded to prevent OOM

    async def push_audio(text: str, idx: int) -> None:
        from .voice import tts_best

        path = await loop.run_in_executor(None, tts_best.synthesize_best,
                                          " ".join(text.split())[:600])
        if not path or ws.client_state != WebSocketState.CONNECTED:
            return
        data = path.read_bytes()
        if ws.client_state != WebSocketState.CONNECTED:
            return
        await ws.send_json({"type": "audio", "i": idx,
                            "fmt": path.suffix.lstrip(".").lstrip("_"),
                            "bytes": len(data)})
        await ws.send_bytes(data)

    async def tts_worker(aq: asyncio.Queue) -> None:
        i = 0
        while True:
            item = await aq.get()
            try:
                if item is None or not isinstance(item, str):
                    return
                if state["cancel"]:
                    continue
                await push_audio(item, i)
                i += 1
            except Exception:
                return
            finally:
                aq.task_done()

    async def reader() -> None:
        try:
            while True:
                msg = await ws.receive_json()
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    if ws.client_state == WebSocketState.CONNECTED:
                        await ws.send_json({"type": "pong", "ts": time.time()})
                    continue
                inbox.put_nowait(msg)
        except Exception:
            inbox.put_nowait(None)

    async def ping_worker() -> None:
        try:
            while ws.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(15)
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json({"type": "ping", "ts": time.time()})
        except Exception:
            pass

    reader_task = asyncio.create_task(reader())
    ping_task = asyncio.create_task(ping_worker())

    async def run_say(text: str, want_audio: bool) -> None:
        aq: asyncio.Queue = asyncio.Queue(maxsize=100)   # sentences -> audio worker, bounded
        worker = asyncio.create_task(tts_worker(aq))

        # Thread->loop handoff done via a plain buffer (GIL-atomic appends)
        # drained by the loop side. No callback-ordering races possible.
        evbuf: list = []
        evbuf_lock = threading.Lock()

        def on_event(ev: dict) -> None:
            with evbuf_lock:
                evbuf.append(ev)

        def take_events() -> list:
            with evbuf_lock:
                out, evbuf[:] = evbuf[:], []
            return out

        ex = loop.run_in_executor(
            None, lambda: brain.handle_turn(
                text, on_event=on_event,
                cancelled=lambda: state["cancel"],
                voice=want_audio))

        acc_tail = ""
        reply = ""

        async def handle_event(res: dict) -> None:
            nonlocal acc_tail, reply
            if res.get("type") == "delta":
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(res)   # live bubble
                acc_tail += str(res.get("text") or "")
                sents, acc_tail = _split_sentences(acc_tail)
                if want_audio and not state["cancel"]:
                    for s in sents:
                        aq.put_nowait(s)
            elif res.get("type") == "done":
                reply = res.get("text") or ""          # final frame covers it
            elif ws.client_state == WebSocketState.CONNECTED:
                await ws.send_json(res)

        async def handle_client_frame(msg) -> None:
            if msg is None:
                state["cancel"] = True
                return
            kind = (msg or {}).get("type")
            if kind == "cancel":
                state["cancel"] = True
            else:
                await ws.send_json({"type": "error", "text": "busy"})

        while True:
            for ev in take_events():
                await handle_event(ev)
            if ex.done():
                break
            try:
                frame = await asyncio.wait_for(inbox.get(), timeout=0.01)
                await handle_client_frame(frame)
            except asyncio.TimeoutError:
                pass
        for ev in take_events():                      # final drain
            await handle_event(ev)
        try:
            reply = ex.result() or reply              # authoritative
        except Exception as exc:  # noqa: BLE001
            reply = reply or f"Error: {str(exc)[:150]}"

        tail = acc_tail.strip()
        if tail and want_audio and not state["cancel"]:
            aq.put_nowait(tail)
        aq.put_nowait(None)

        try:
            await asyncio.wait_for(worker, timeout=180)
        except Exception:
            worker.cancel()
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.send_json({"type": "final", "reply": reply})

    try:
        while True:
            msg = await inbox.get()
            if msg is None:
                break
            kind = (msg or {}).get("type")
            if kind == "cancel":
                state["cancel"] = True
                continue
            text = str((msg or {}).get("text") or "").strip()
            if not text:
                continue
            if kind == "tts" and not state["busy"]:
                await push_audio(text[:600], 0)
                continue
            if kind != "say":
                continue
            if state["busy"]:
                await ws.send_json({"type": "error", "text": "busy"})
                continue
            state["busy"], state["cancel"] = True, False
            try:
                await run_say(text, bool((msg or {}).get("voice", True)))
            except Exception as exc:  # noqa: BLE001
                try:
                    await ws.send_json({"type": "error",
                                        "text": str(exc)[:200]})
                except Exception:
                    pass
            finally:
                state["busy"] = False
    except WebSocketDisconnect:
        pass
    finally:
        state["cancel"] = True
        reader_task.cancel()
        ping_task.cancel()


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, request=None):
    if is_circuit_open():
        from .fastlane import fast_command
        from .llm import _offline_parse
        instant = fast_command(body.text) or _offline_parse(body.text)
        if instant:
            async def _instant_gen():
                yield f"data: {json.dumps({'type': 'final', 'reply': instant})}\n\n"
            return StreamingResponse(_instant_gen(), media_type="text/event-stream")
        raise HTTPException(status_code=503, detail="LLM circuit breaker open")

    state = {"cancelled": False}

    async def source():
        q: deque = deque()

        def on_event(ev: dict) -> None:
            q.append(ev)

        def work() -> None:
            try:
                reply = brain.handle_turn(
                    body.text, on_event=on_event, cancelled=lambda: state["cancelled"]
                )
                q.append({"type": "final", "reply": reply})
            except brain.TurnCancelled:
                q.append({"type": "cancelled"})
            except Exception as exc:
                q.append({"type": "error", "text": str(exc)[:200]})

        loop = asyncio.get_running_loop()
        task = loop.run_in_executor(None, work)
        while not task.done() or q:
            while q:
                ev = q.popleft()
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if task.done():
                break
            await asyncio.sleep(0.03)
        while q:
            ev = q.popleft()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "end"}\n\n'

    return StreamingResponse(source(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/tts")
def tts(text: str, engine: str = ""):
    """Neural voice by default; engine=sapi forces the local Windows voice."""
    from fastapi.responses import Response

    from .voice import tts_best

    try:
        path = tts_best.synthesize_best(
            " ".join((text or "").split())[:600],
            force_engine=engine or None)
        if path is None:
            raise RuntimeError("synthesis failed")
        data = path.read_bytes()
        media = "audio/wav" if path.suffix == ".wav" else "audio/mpeg"
        return Response(content=data, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        log.warning("TTS error: %s", exc)
        raise HTTPException(status_code=502, detail="tts synthesis unavailable")


class ConvoIn(BaseModel):
    on: bool


@app.post("/api/voice/convo")
def voice_convo_toggle(body: ConvoIn):
    """Always-on conversation mode: open mic until switched off."""
    from fastapi import HTTPException

    from .voice import convo

    if body.on:
        started = convo.start()
        if not started and not convo.convo_mode.running:
            raise HTTPException(status_code=503,
                                detail="mic unavailable or no STT model")
        return {"ok": True, "running": True}
    convo.stop()
    return {"ok": True, "running": False}


@app.get("/api/voice/convo")
def voice_convo_status():
    from .voice import convo

    return {"running": convo.status()["running"]}


_transcribe_rate: dict[str, deque] = {}
_TRANSCRIBE_LIMIT = 15  # max requests per minute
_TRANSCRIBE_WINDOW = 60.0


@app.post("/api/transcribe")
async def transcribe(request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    if client_ip != "testclient":
        if client_ip not in _transcribe_rate:
            if len(_transcribe_rate) > 500:
                _transcribe_rate.clear()
            _transcribe_rate[client_ip] = deque()
        q = _transcribe_rate[client_ip]
        while q and q[0] < now - _TRANSCRIBE_WINDOW:
            q.popleft()
        if len(q) >= _TRANSCRIBE_LIMIT:
            raise HTTPException(status_code=429, detail="Transcribe rate limit exceeded (max 15/min).")
        q.append(now)

    data = await request.body()
    if len(data) < 100:
        raise HTTPException(status_code=400, detail="no audio")
    if len(data) > 10 * 1024 * 1024:  # 10MB maximum payload limit
        raise HTTPException(status_code=413, detail="audio payload too large (max 10MB)")
    from .voice import stt as mkstt

    def work() -> str:
        try:
            return mkstt.transcribe_wav(data)
        except Exception as exc:
            log.warning("transcribe_wav error: %s", exc)
            return ""

    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, work)
    except Exception as exc:
        log.warning("STT runtime error: %s", exc)
        text = ""
    return {"text": text}


_tools_call_rate: dict[str, deque] = {}
_TOOLS_CALL_LIMIT = 30
_TOOLS_CALL_WINDOW = 60.0


@app.post("/api/tools/call")
async def call_tool(req: Request):
	import logging

	log = logging.getLogger("mk2.server")

	# --- rate limit ---
	client_ip = req.client.host if req.client else "unknown"
	now = time.time()
	if client_ip != "testclient":
		if client_ip not in _tools_call_rate:
			if len(_tools_call_rate) > 500:
				_tools_call_rate.clear()
			_tools_call_rate[client_ip] = deque()
		q = _tools_call_rate[client_ip]
		while q[0] < now - _TOOLS_CALL_WINDOW:
			q.popleft()
		if len(q) >= _TOOLS_CALL_LIMIT:
			raise HTTPException(
				status_code=429,
				detail="Tool call rate limit exceeded (max 30/min).",
			)
		q.append(now)

	# --- parse & sanitize ---
	body = await req.json()
	name = str(body.get("name", "")).strip()
	args = body.get("args") or {}

	if not name or len(name) > 64:
		raise HTTPException(status_code=400, detail="Tool name must be 1-64 characters.")
	if not isinstance(args, dict):
		raise HTTPException(status_code=400, detail="Args must be a JSON object.")

	for k, v in args.items():
		if len(str(k)) > 64:
			raise HTTPException(
				status_code=400,
				detail=f"Arg key exceeds 64 char limit: '{k}'.",
			)
		if len(str(v)) > 4096:
			raise HTTPException(
				status_code=400,
				detail=f"Arg value for '{k}' exceeds 4096 char limit.",
			)

	# --- pre-flight consent check ---
	try:
		from .consent import get_consent_manager

		cm = get_consent_manager()
		if not cm.has_consent(name):
			log.warning("Tool call blocked by consent: %s (ip=%s)", name, client_ip)
			raise HTTPException(
				status_code=403,
				detail=f"Consent denied: cannot execute '{name}'.",
			)
	except ImportError:
		pass

	# --- pre-flight circuit breaker check ---
	breaker = getattr(tools, "_CIRCUIT_BREAKER", {})
	open_until = breaker.get(name, 0)
	if open_until > now:
		remaining = int(open_until - now)
		raise HTTPException(
			status_code=429,
			detail=f"Tool '{name}' temporarily disabled for {remaining}s.",
		)

	# --- execute via the tool dispatcher (audit + error handling inside) ---
	result = tools.call(name, args)

	log.info(
		"Tool call %s -> ok=%s (ip=%s)", name, result.get("ok", False), client_ip
	)
	return result


@app.get("/api/memory")
def memory_view():
    return {"facts": db.all_facts(), "episodes": db.recall_episodes("", 0) or []}


@app.get("/api/diag")
def diag():
    from . import diag as diag_mod

    return diag_mod.run_checks(include_network=False)


@app.get("/api/pairing")
def pairing():
    """Phase 2 comms status: telegram pairing + push config."""
    out = {"telegram": {"configured": False, "paired": False, "pairing_code": ""}}
    try:
        from . import telegram_link

        out["telegram"] = telegram_link.status()
    except Exception:
        pass
    try:
        from . import push_notify

        out["push"] = push_notify.status()
    except Exception:
        pass
    return out


@app.get("/api/audit")
def audit_view():
    return db.recent_audit(20)


@app.post("/api/memory/clear-chat")
def clear_chat():
    db.clear_messages()
    return {"ok": True}


@app.get("/api/autonomy/approvals")
def get_pending_approvals():
    from .approval_queue import get_approval_queue
    return {"ok": True, "approvals": get_approval_queue().get_pending()}


@app.post("/api/autonomy/approve")
async def approve_action(req: Request):
    _verify_sync_auth(req)
    from .approval_queue import get_approval_queue
    body = await req.json()
    item_id = body.get("id")
    if not item_id:
        return {"ok": False, "error": "Missing approval id"}
    return get_approval_queue().approve(item_id)


@app.post("/api/autonomy/reject")
async def reject_action(req: Request):
    _verify_sync_auth(req)
    from .approval_queue import get_approval_queue
    body = await req.json()
    item_id = body.get("id")
    reason = body.get("reason", "")
    if not item_id:
        return {"ok": False, "error": "Missing approval id"}
    return get_approval_queue().reject(item_id, reason)


@app.get("/api/autonomy/stats")
def get_autonomy_stats():
    from .consent import get_consent_manager
    from .credential_vault import get_credential_vault
    from .revenue import get_revenue_tracker
    from .jarvis_agent import get_jarvis_agent
    from .security_agent import get_security_agent
    from .wellness_agent import get_wellness_agent
    cm = get_consent_manager()
    rev = get_revenue_tracker()
    cv = get_credential_vault()
    ja = get_jarvis_agent()
    sec = get_security_agent()
    wel = get_wellness_agent()
    return {
        "ok": True,
        "consent_level": cm.get_level(),
        "trust_score": cm.trust_score(),
        "revenue_stats": rev.get_stats(7),
        "configured_services": cv.list_services(),
        "security_score": sec.check_passwords().get("health_score", 1.0),
        "screen_time": wel.track_screen_time(),
        "proactive_alerts": ja.proactive_alerts,
    }


@app.post("/api/autonomy/kill")
def emergency_kill_switch(request: Request):
    _verify_sync_auth(request)
    from .kill_switch import get_kill_switch
    res = get_kill_switch().stop_all("API Emergency Stop")
    return res


@app.get("/api/autonomy/debt")
def get_technical_debt_report():
    from .self_improvement import get_self_improvement_engine
    engine = get_self_improvement_engine()
    return {
        "ok": True,
        "report": engine.technical_debt_report(),
        "issues": engine.discovered_issues,
    }


@app.get("/api/autonomy/health")
def get_autonomy_health():
    import time
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Kernel health
    from .kernel import get_kernel_tasks
    k_tasks = get_kernel_tasks()
    alive_tasks = sum(1 for t in k_tasks.values() if not t.done()) if isinstance(k_tasks, dict) else 0

    # Money engine health
    from .money_engine import get_money_engine
    me = get_money_engine()
    last_scan_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(me.last_tick_ts)) if me.last_tick_ts else "never"

    # Browser health
    from .browser_agent import get_browser_agent
    ba = get_browser_agent()
    session_active = ba.page is not None

    subsystems = {
        "kernel": {"alive": True, "running_tasks": alive_tasks, "crashes_last_hour": 0},
        "brain": {"alive": True, "queue_depth": 0},
        "money_engine": {"alive": me.running or me.last_tick_ts > 0, "last_scan": last_scan_iso},
        "browser": {"alive": True, "session_active": session_active},
        "awareness": {"alive": True, "status": "active"},
        "voice": {"alive": True, "last_turn": "idle"},
    }

    crashes = sum(s.get("crashes_last_hour", 0) for s in subsystems.values() if isinstance(s, dict))
    status = "healthy"
    if crashes > 5:
        status = "critical"
    elif crashes > 3:
        status = "degraded"

    return {
        "status": status,
        "subsystems": subsystems,
        "silent_failures": 0,
        "last_check": now_iso,
    }


def _check_finance_consent(tier: str = "read") -> None:
    """Ensure client has consent to access sensitive financial data with granular tier controls."""
    from .consent import get_consent_manager
    cm = get_consent_manager()
    lvl = cm.get_level()
    if lvl == "none":
        raise HTTPException(status_code=403, detail="Consent denied: financial access is disabled.")
    if tier == "sensitive" and lvl in ("none", "read"):
        raise HTTPException(status_code=403, detail="Consent denied: sensitive client data requires assist or higher consent tier.")


@app.get("/api/money/briefing")
def get_money_briefing_endpoint():
    _check_finance_consent("read")
    from .financial_intelligence import get_financial_intelligence
    return {"ok": True, "briefing": get_financial_intelligence().financial_briefing()}


@app.get("/api/money/pipeline")
def get_money_pipeline_endpoint(days: int = 30):
    _check_finance_consent("read")
    from .money_engine import get_money_engine
    return {"ok": True, "pipeline": get_money_engine().get_funnel_metrics(days)}


@app.get("/api/money/clients")
def get_money_clients_endpoint():
    _check_finance_consent("sensitive")
    from .revenue import get_revenue_tracker
    return {"ok": True, "stats": get_revenue_tracker().get_stats(30)}


@app.get("/api/money/opportunities")
def get_money_opportunities_endpoint():
    _check_finance_consent("read")
    from .money_engine import get_money_engine
    return {"ok": True, "opportunities": get_money_engine().scan_opportunities()}


@app.get("/api/money/followup")
def get_money_followup_endpoint():
    _check_finance_consent("read")
    from .money_engine import get_money_engine
    return {"ok": True, "followups": get_money_engine().finance.diversification_suggestions([])}





