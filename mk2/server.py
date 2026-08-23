"""FastAPI surface: streaming chat, health, memory/audit views, static UI."""
import asyncio
import json
import queue as _queue
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import brain, config, db, llm, tools

app = FastAPI(title="EVO MK2")
UI = Path(config.UI_DIR)

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


@app.get("/api/memory")
def memory_view():
    return {"facts": db.all_facts(), "episodes": db.recall_episodes("", 0) or []}


@app.get("/api/audit")
def audit_view():
    return db.recent_audit(20)


@app.post("/api/memory/clear-chat")
def clear_chat():
    db.clear_messages()
    return {"ok": True}

