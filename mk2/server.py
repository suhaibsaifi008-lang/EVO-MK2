"""FastAPI surface: streaming chat, health, memory/audit views, static UI."""
import asyncio
import json
import queue as _queue
import re
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from . import brain, config, db, llm, tools

app = FastAPI(title="EVO MK2")
UI = Path(config.UI_DIR)

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
    reply = brain.handle_turn(body.text)
    return {"reply": reply}


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
                inbox.put_nowait(await ws.receive_json())
        except Exception:
            inbox.put_nowait(None)

    reader_task = asyncio.create_task(reader())

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
                cancelled=lambda: state["cancel"]))

        acc_tail = ""
        reply = ""

        def handle_event(res: dict) -> None:
            nonlocal acc_tail, reply
            if res.get("type") == "delta":
                if ws.client_state == WebSocketState.CONNECTED:
                    asyncio.ensure_future(ws.send_json(res))   # live bubble
                acc_tail += str(res.get("text") or "")
                sents, acc_tail = _split_sentences(acc_tail)
                if want_audio and not state["cancel"]:
                    for s in sents:
                        aq.put_nowait(s)
            elif res.get("type") == "done":
                reply = res.get("text") or ""          # final frame covers it
            elif ws.client_state == WebSocketState.CONNECTED:
                asyncio.ensure_future(ws.send_json(res))

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
                handle_event(ev)
            if ex.done():
                break
            try:
                frame = await asyncio.wait_for(inbox.get(), timeout=0.01)
                await handle_client_frame(frame)
            except asyncio.TimeoutError:
                pass
        for ev in take_events():                      # final drain
            handle_event(ev)
        try:
            reply = ex.result() or reply              # authoritative
        except Exception as exc:  # noqa: BLE001
            reply = reply or f"Error: {str(exc)[:150]}"
        tail = acc_tail.strip()
        if tail and want_audio and not state["cancel"]:
            aq.put_nowait(tail)
        aq.put_nowait(None)

        try:                                       # flush ALL audio first
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

