"""FastAPI surface: streaming chat, health, memory/audit views, static UI."""
import asyncio
import json
import queue as _queue
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import brain, config, db, llm, tools

app = FastAPI(title="EVO MK2")
UI = Path(config.UI_DIR)

from . import events_api as _events  # noqa: E402

_events.register(app)

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
    return {
        "ok": True,
        "name": config.settings.name,
        "llm_online": _llm_online_cached(),
        "providers": [p["name"] for p in llm._providers()],
        "tools": len(tools.manifest()),
        "voice": voice,
        "version": "mk2-0.1",
    }


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    reply = brain.handle_turn(body.text)
    return {"reply": reply}


@app.post("/api/chat/stream")
async def chat_stream(body: ChatIn, request=None):
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

