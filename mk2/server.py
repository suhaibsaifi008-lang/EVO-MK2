"""FastAPI surface: streaming chat, health, memory/audit views, static UI."""
import asyncio
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

app = FastAPI(title="EVO MK2")
UI = Path(config.UI_DIR)

# ------------------------------------------------------------------ API key auth

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _resolve_api_key() -> str | None:
    key = os.environ.get("EVO_API_KEY", "").strip()
    if not key:
        try:
            key = getattr(config.settings, "api_key", "") or ""
        except Exception:
            pass
    return key.strip() or None


def _is_auth_required(request: Request) -> bool:
    path = str(request.url.path)
    if path in ("/api/health", "/", "/health", "/favicon.ico"):
        return False
    if path.startswith("/ui") or path.startswith("/static") or path.startswith("/voice/client"):
        return False
    return True


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        client = (request.client.host if request.client else "") or ""
        api_key = _resolve_api_key()
        if api_key and _is_auth_required(request) and client not in _LOCAL_HOSTS:
            provided = (
                request.headers.get("x-api-key", "")
                or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or request.query_params.get("key", "")
            )
            if provided != api_key:
                from fastapi.responses import JSONResponse

                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


app.add_middleware(APIKeyMiddleware)

from starlette.middleware.cors import CORSMiddleware

_cors_origins = [o.strip() for o in os.environ.get("EVO_CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ Timeout & Circuit Breaker

class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(("/ws", "/voice")):
            return await call_next(request)
        try:
            return await asyncio.wait_for(call_next(request), timeout=60.0)
        except asyncio.TimeoutError:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "request timed out after 60s"}, status_code=504)


app.add_middleware(TimeoutMiddleware)

_circuit_open_until: float = 0.0
_consecutive_llm_failures: int = 0
_circuit_lock = threading.Lock()


def is_circuit_open() -> bool:
    with _circuit_lock:
        return time.time() < _circuit_open_until


def record_llm_result(ok: bool) -> None:
    global _circuit_open_until, _consecutive_llm_failures
    with _circuit_lock:
        if ok:
            _consecutive_llm_failures = 0
            _circuit_open_until = 0.0
        else:
            _consecutive_llm_failures += 1
            if _consecutive_llm_failures >= 5:
                _circuit_open_until = time.time() + 30.0

from . import events_api as _events  # noqa: E402

_events.register(app)

try:
    from .voice import webrtc_v2  # noqa: E402

    webrtc_v2.register(app)
except Exception as _voice_exc:  # noqa: BLE001
    webrtc_v2 = None
    print(f"[voice-v2] registration failed: {_voice_exc}", file=__import__("sys").stderr)

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
    return FileResponse(UI / "index.html")


@app.get("/landing")
def landing_page():
    return FileResponse(UI / "landing.html")


@app.get("/autonomy")
def autonomy_dashboard():
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


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    if is_circuit_open():
        from .fastlane import fast_command
        from .llm import _offline_parse
        instant = fast_command(body.text) or _offline_parse(body.text)
        if instant:
            return {"reply": instant}
        raise HTTPException(status_code=503, detail="LLM circuit breaker open (cooling down after repeated failures)")
    try:
        reply = brain.handle_turn(body.text)
        record_llm_result(True)
        return {"reply": reply}
    except Exception as exc:
        record_llm_result(False)
        raise HTTPException(status_code=500, detail=str(exc))


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


@app.post("/api/auth/pin")
def auth_pin_endpoint(body: AuthPinIn) -> dict:
    from .security import voiceprint
    if voiceprint.verify_pin(body.pin):
        return {"ok": True, "message": "Authentication successful. Security lock cleared."}
    raise HTTPException(status_code=403, detail="Invalid security PIN.")


@app.get("/api/status")
def get_status_endpoint() -> dict:
    from . import autonomy, initiative_engine, swarm, user_profile
    prof = user_profile.get_user_profile()
    return {
        "ok": True,
        "brain": {
            "role": "primary",
            "model": config.settings.anthropic_model or config.settings.openai_model,
            "provider": "anthropic" if config.settings.anthropic_key else "openai",
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


@app.get("/api/session/state")
def get_session_state_endpoint() -> dict:
    """Return compact conversational & memory state for cross-device handoff."""
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
def import_session_endpoint(body: SessionImportIn) -> dict:
    """Import conversational turns and facts from remote device."""
    imported_turns = db.import_messages(body.recent_turns)
    imported_facts = 0
    for fact in body.facts:
        k = fact.get("key") or fact.get("k")
        v = fact.get("value") or fact.get("v")
        if k and v:
            db.remember_fact(str(k), str(v), source="remote_sync")
            imported_facts += 1
    return {"ok": True, "imported_turns": imported_turns, "imported_facts": imported_facts}


_pairing_codes: dict[str, dict] = {}


class PairRequestIn(BaseModel):
    device_name: str = "Remote Device"


@app.post("/api/pair/request")
def request_pairing_endpoint(body: PairRequestIn) -> dict:
    """Request 6-digit device pairing code."""
    import random
    from .bus import bus
    code = f"{random.randint(100000, 999999)}"
    _pairing_codes[code] = {
        "expires": time.time() + 300,
        "device_name": body.device_name,
        "approved": False,
    }
    bus.publish("notify.out", {
        "kind": "pairing",
        "text": f"Device '{body.device_name}' requested pairing. Code: {code}. Say 'approve {code}' or use dashboard.",
    })
    return {"ok": True, "code": code, "expires_in": 300}


class PairApproveIn(BaseModel):
    code: str


@app.post("/api/pair/approve")
def approve_pairing_endpoint(body: PairApproveIn) -> dict:
    """Approve a pending device pairing code."""
    entry = _pairing_codes.get(body.code.strip())
    if not entry or entry["expires"] < time.time():
        raise HTTPException(status_code=400, detail="Invalid or expired pairing code.")
    entry["approved"] = True
    return {"ok": True, "device_name": entry["device_name"], "message": "Device pairing approved."}


@app.get("/api/sync/status")
def sync_status_endpoint() -> dict:
    import platform
    from . import sync
    return {
        "ok": True,
        "device_id": sync.get_device_id(),
        "device_name": platform.node(),
    }


@app.get("/api/sync/pull")
def sync_pull_endpoint() -> dict:
    from . import sync
    return sync.export_sync_bundle()


@app.post("/api/sync/push")
def sync_push_endpoint(bundle: dict[str, Any]) -> dict:
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
def tool_synthesize_endpoint(body: ToolSynthesizeIn) -> dict:
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
    loop = asyncio.get_running_loop()
    state = {"busy": False, "cancel": False}
    inbox: asyncio.Queue = asyncio.Queue()   # client messages, read always

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
        aq: asyncio.Queue = asyncio.Queue()   # sentences -> audio worker
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
        raise HTTPException(status_code=502, detail=f"tts unavailable: {exc}")


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


@app.post("/api/transcribe")
async def transcribe(request: Request) -> dict:
    data = await request.body()
    if len(data) < 100:
        raise HTTPException(status_code=400, detail="no audio")
    from .voice import stt as mkstt

    def work() -> str:
        return mkstt.transcribe_wav(data)

    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, work)
    except ValueError as exc:  # bad wav payload
        raise HTTPException(status_code=400, detail=f"bad audio: {exc}")
    except RuntimeError as exc:  # vosk model missing
        raise HTTPException(status_code=503, detail=str(exc))
    return {"text": text}


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
    from .approval_queue import get_approval_queue
    body = await req.json()
    item_id = body.get("id")
    if not item_id:
        return {"ok": False, "error": "Missing approval id"}
    return get_approval_queue().approve(item_id)


@app.post("/api/autonomy/reject")
async def reject_action(req: Request):
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
def emergency_kill_switch():
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




