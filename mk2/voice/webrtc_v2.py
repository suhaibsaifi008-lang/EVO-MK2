"""Voice v2: the fast Pipecat voice channel, embedded in the MK2 kernel.

Browser mic -> SmallWebRTC -> Silero VAD -> faster-whisper small.en
-> FreeLLMAPI (primary model, not fast) -> Kokoro TTS
-> WebRTC speakers.

Unlike the old wake-word gateway this runs INSIDE the console server, on the
same port and in the same process as everything else:

 python run.py -> console http://127.0.0.1:8421/
 voice http://127.0.0.1:8421/voice/client/

Integration points with the MK2 brain:
- persona_block() + truth_law() shape every ,
- curated tool subset executed through the audited tools registry
 (env EVO_VOICE_TOOLS overrides the list),
- every completed turn flows through memory.record_turn(surface="voice")
 so facts/style-feedback/episodic memory stay in sync, and onto the event
 bus as "voice.turn" so the console can follow the conversation live.

The direct LLM call (bypassing brain.handle_turn) is deliberate: voice needs
first-token latency over orchestration depth; measured TTFT floor is the
provider's ~1-3s, anything the brain adds on top was pure loss.
"""
import asyncio
import os
import threading
import uuid

from fastapi import HTTPException

from .. import config

_state = {
    "available": False, # pipecat + aiortc importable
    "enabled": False, # registered on the running app
    "error": "",
    "sessions": set(), # active session ids
}
_lock = threading.Lock()

CLIENT_PATH = "/voice/client/"

DEFAULT_TOOLS = ("web_search", "deep_thought", "task_start",
    "reminder_add_tool", "youtube_summarize", "docs_create")


def _tool_names() -> tuple[str, ...]:
    raw = os.environ.get("EVO_VOICE_TOOLS", "")
    names = tuple(t.strip() for t in raw.split(",") if t.strip())
    return names or DEFAULT_TOOLS


def status() -> dict:
    with _lock:
        return {"available": _state["available"],
            "enabled": _state["enabled"],
            "client_url": CLIENT_PATH if _state["enabled"] else "",
            "active_sessions": len(_state["sessions"]),
            "error": _state["error"]}


# ----------------------------------------------------------------------
# LLM plumbing (persona + truth law + audited tools)
# ----------------------------------------------------------------------

def system_instruction() -> str:
    from ..persona_loader import ensure_persona, persona_block, truth_law
    from .. import db, vault as vault_mod

    ensure_persona()
    tool_lines = "\n".join(f"- {n}" for n in _tool_names())

    # Build memory context from stored facts, vault notes, and standing rules
    try:
        facts = db.all_facts(18)
    except Exception:
        facts = []
    try:
        notes = vault_mod.list_notes()[:12]
    except Exception:
        notes = []
    try:
        rule_facts = [v for k, v in db.all_facts(40).items() if k.startswith("rule:")]
    except Exception:
        rule_facts = []

    memory_ctx = ""
    if facts:
        memory_ctx += "USER FACTS:\n" + "\n".join(f"- {f}" for f in facts) + "\n"
    if notes:
        memory_ctx += "NOTES:\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    if rule_facts:
        memory_ctx += "RULES:\n" + "\n".join(f"- {r}" for r in rule_facts) + "\n"

    return (
        persona_block(max_chars=700)
        + "\n" + memory_ctx
        + "\n" + truth_law()
        + "\nYou are on a voice channel. Keep replies SHORT (1-4 sentences), "
            "natural spoken language, no lists, no markdown, no emojis. "
            "NEVER say 'As an AI' or mention internal steps, tool names, or providers. "
            "Sound like a real person talking on a phone call. "
            "If you mention options or points, list them right there - never announce a list without listing it. "
            "You can call these tools when needed:\n"
        + tool_lines
    )


def _tool_fn(name: str):
    async def _fn(**kwargs):
        try:
            result = await asyncio.to_thread(_call_tool_checked, name, kwargs)
            return str(result.get("speech", ""))[:900]
        except Exception as exc: # noqa: BLE001
            return f"Tool failed: {str(exc)[:150]}"
    _fn.__name__ = name
    _fn.__doc__ = name
    return _fn


def _call_tool_checked(name: str, kwargs: dict) -> dict:
    """Route through the registry so permissions + audit ledger apply."""
    from .. import tools

    allowed = _tool_names()
    if name not in allowed:
        return {"ok": False, "speech": f"Voice may not use '{name}'.", "data": {}}
    return tools.call(name, kwargs)


# ----------------------------------------------------------------------
# Turn recorder: voice turns -> MK2 memory + event bus
# ----------------------------------------------------------------------

class TurnRecorder:
    """Buffers one user/assistant exchange and flushes it to memory+bus.

    Kept free of pipecat types so it is unit-testable without audio stack.
    """

    def __init__(self, surface: str = "voice") -> None:
        self.surface = surface
        self.user_parts: list[str] = []
        self.assistant_parts: list[str] = []

    def note_user(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._flush_if_pending()
            self.user_parts.append(text)

    def note_assistant(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.assistant_parts.append(text)

    def pending(self) -> bool:
        return bool(self.user_parts or self.assistant_parts)

    def flush(self) -> None:
        user = " ".join(self.user_parts).strip()
        reply = " ".join(self.assistant_parts).strip()
        self.user_parts.clear()
        self.assistant_parts.clear()
        if not (user or reply):
            return
        try:
            from .. import memory

            threading.Thread(
                target=_record_turn_safe,
                args=(user, reply, self.surface), daemon=True,
                name="mk2-voice-record").start()
        except Exception:
            pass
        self._publish(user, reply)

    def _flush_if_pending(self) -> None:
        if self.assistant_parts:
            self.flush()

    @staticmethod
    def _publish(user: str, reply: str) -> None:
        try:
            from ..bus import bus

            bus.publish("voice.turn", {"user": user[:400], "reply": reply[:900]})
        except Exception:
            pass


def _record_turn_safe(user: str, reply: str, surface: str) -> None:
    try:
        from .. import memory

        memory.record_turn(user, reply, surface)
    except Exception:
        pass


def _make_pipecat_recorder():
    """FrameProcessor capturing Transcription/Text frames via TurnRecorder."""
    from pipecat.frames.frames import (EndFrame, StartInterruptionFrame,
        TextFrame, TranscriptionFrame)
    from pipecat.processors.frame_processor import FrameProcessor

    class _Recorder(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.rec = TurnRecorder()

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TranscriptionFrame):
                self.rec.note_user(getattr(frame, "text", ""))
            elif isinstance(frame, TextFrame):
                self.rec.note_assistant(getattr(frame, "text", ""))
            elif isinstance(frame, (StartInterruptionFrame, EndFrame)):
                self.rec.flush()
            await self.push_frame(frame, direction)

    return _Recorder


# ----------------------------------------------------------------------
# Pipeline
# ----------------------------------------------------------------------

def _transport_params():
    from pipecat.transports.base_transport import TransportParams

    return TransportParams(audio_in_enabled=True, audio_out_enabled=True)


async def run_pipeline(transport) -> None:
    """Build and run the voice pipeline on an existing transport."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.services.kokoro.tts import KokoroTTSService
    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.whisper.stt import WhisperSTTService

    from ..config import settings

    whisper_model = os.environ.get("EVO_STT_MODEL", "small.en")
    tts_voice = os.environ.get("EVO_TTS_VOICE", "af_heart")
    # Use the PRIMARY model for voice - smarter, not just fast
    llm_model = settings.openai_model

    stt = WhisperSTTService(model=whisper_model, compute_type="int8")
    tts = KokoroTTSService(settings=KokoroTTSService.Settings(voice=tts_voice))
    llm = OpenAILLMService(
        api_key=settings.openai_key,
        base_url=settings.openai_base,
        model=llm_model,
        params=None,
        system_instruction=system_instruction(),
    )

    names = _tool_names()
    context = LLMContext(messages=[], tools=[_tool_fn(n) for n in names])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )
    recorder = _make_pipecat_recorder()()

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        recorder,
        tts,
        transport.output(),
        assistant_aggregator,
    ])
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    @transport.event_handler("on_client_connected")
    async def _connected(transport, client): # noqa: ANN001
        print("[voice-v2] client connected", flush=True)

    @transport.event_handler("on_client_disconnected")
    async def _disconnected(transport, client): # noqa: ANN001
        print("[voice-v2] client disconnected", flush=True)
        recorder.rec.flush()
        await task.cancel()

    runner = PipelineRunner(name="EVOVoiceV2")
    await runner.run(task)


async def bot(runner_args) -> None:
    """pipecat-runner entrypoint (standalone `py -3 voice_bot.py` path)."""
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    transport = SmallWebRTCTransport(
        webrtc_connection=runner_args.webrtc_connection,
        params=_transport_params())
    await run_pipeline(transport)


async def _run_session(connection, body: dict, session_id: str) -> None:
    """One WebRTC session: build transport around the negotiated connection."""
    from pipecat.runner.types import SmallWebRTCRunnerArguments
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    with _lock:
        _state["sessions"].add(session_id)
    try:
        runner_args = SmallWebRTCRunnerArguments(
            webrtc_connection=connection, body=body, session_id=session_id)
        transport = SmallWebRTCTransport(
            webrtc_connection=connection, params=_transport_params())
        await run_pipeline(transport)
    except Exception as exc: # noqa: BLE001
        _state["error"] = str(exc)[:200]
        print(f"[voice-v2] pipeline error: {exc}", flush=True)
    finally:
        with _lock:
            _state["sessions"].discard(session_id)


# ----------------------------------------------------------------------
# FastAPI wiring
# ----------------------------------------------------------------------

def register(app) -> dict:
    """Mount voice v2 on the console app. Heavy imports stay lazy so tests
    and non-audio machines keep working; returns a status dict."""
    enabled = os.environ.get("EVO_VOICE_V2", "1") == "1"
    if not enabled:
        return status()
    try:
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.request_handler import (
            SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )

        available = True
    except Exception as exc: # noqa: BLE001
        with _lock:
            _state.update(available=False,
                error=f"pipecat webrtc extras missing: {exc}"[:200])
        return status()

    handler = SmallWebRTCRequestHandler() # MULTIPLE mode: tab refresh safe

    @app.post("/start")
    async def voice_start(request: dict):
        """Prebuilt-client handshake (mirrors pipecat runner contract)."""
        if (request or {}).get("transport", "webrtc") != "webrtc":
            raise HTTPException(status_code=400,
                detail="only the 'webrtc' transport is supported")
        session_id = str(uuid.uuid4())
        result = {"status": "ready", "sessionId": session_id,
            "transports": ["webrtc"]}
        if (request or {}).get("enableDefaultIceServers"):
            result["iceConfig"] = {"iceServers": [
                {"urls": ["stun:stun.l.google.com:19302"]}]}
        return result

    @app.get("/status")
    async def voice_status():
        return {"status": "ready", "transports": ["webrtc"]}

    async def _offer(request: SmallWebRTCRequest, session_id: str | None):
        resolved = session_id or str(uuid.uuid4())

        async def on_connection(connection: SmallWebRTCConnection):
            asyncio.create_task(_run_session(connection, {}, resolved))

        answer = await handler.handle_web_request(
            request=request, webrtc_connection_callback=on_connection)
        return answer

    @app.post("/api/offer")
    async def offer_direct(request: SmallWebRTCRequest):
        return await _offer(request, None)

    @app.patch("/api/offer")
    async def offer_patch(patch: dict):
        from pipecat.transports.smallwebrtc.request_handler import (
            IceCandidate, SmallWebRTCPatchRequest)

        req = SmallWebRTCPatchRequest(pc_id=patch.get("pc_id", ""),
            candidates=[IceCandidate(**c) for c in
                patch.get("candidates", [])])
        await handler.handle_patch_request(req)
        return {"status": "success"}

    @app.post("/sessions/{session_id}/api/offer")
    async def offer_session(session_id: str, request: SmallWebRTCRequest):
        return await _offer(request, session_id)

    @app.patch("/sessions/{session_id}/api/offer")
    async def offer_session_patch(session_id: str, patch: dict):
        from pipecat.transports.smallwebrtc.request_handler import (
            IceCandidate, SmallWebRTCPatchRequest)

        req = SmallWebRTCPatchRequest(pc_id=patch.get("pc_id", ""),
            candidates=[IceCandidate(**c) for c in
                patch.get("candidates", [])])
        await handler.handle_patch_request(req)
        return {"status": "success"}

    # Prebuilt client UI (relative asset paths -> mount-safe under /voice).
    try:
        from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

        @app.get("/voice/client")
        def _client_redirect():
            from fastapi.responses import RedirectResponse

            return RedirectResponse(url=CLIENT_PATH)

        app.mount("/voice/client", PipecatPrebuiltUI, name="voice-client")
    except Exception as exc: # noqa: BLE001
        with _lock:
            _state.update(error=f"prebuilt client unavailable: {exc}"[:200])

    with _lock:
        _state.update(available=True, enabled=True, error="")
    return status()


def boot_line() -> str:
    """Human-readable line for kernel startup log."""
    st = status()
    if st["enabled"]:
        host, port = config.settings.host, config.settings.port
        return f"voice v2 ready: http://{host}:{port}{CLIENT_PATH}"
    return f"voice v2 disabled ({st['error'] or 'EVO_VOICE_V2=0'})"
