"""Gemini Live duplex session (proven MK1 bridge, trimmed for MK2)."""
import asyncio
import queue
import threading
import time

from ..config import settings

OUTPUT_RATE = 24000
INPUT_RATE = 16000

PERSONA = (
    "You are EVO, a warm, sharp personal assistant speaking aloud. Keep replies "
    "natural and concise (1-4 sentences unless depth is requested). Match the "
    "user's tone. Never mention being an AI model or read out URLs."
)
EXIT_WORDS = ("stop listening", "go to sleep", "goodbye", "end session",
              "that will be all")


def available() -> bool:
    import os

    if settings.voice_engine == "local":
        return False
    return bool(settings.gemini_key)


def is_exit(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in EXIT_WORDS)


class Speaker:
    """Plays Gemini 24k PCM; interruptible."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice as sd

            self._stream = sd.OutputStream(samplerate=OUTPUT_RATE, channels=1,
                                           dtype="int16", callback=self._cb,
                                           blocksize=960)
            self._stream.start()
        except Exception:
            self._stream = None

    def _cb(self, outdata, frames, time_info, status):
        need = frames * 2
        with self._lock:
            take = bytes(self._buf[:need])
            del self._buf[:need]
        if len(take) < need:
            take += b"\x00" * (need - len(take))
        outdata[:] = take

    def play(self, data: bytes) -> None:
        with self._lock:
            self._buf.extend(data)
            cap = OUTPUT_RATE * 2 * 15
            if len(self._buf) > cap:
                del self._buf[:-cap]

    def interrupt(self) -> None:
        with self._lock:
            self._buf.clear()

    def stop(self) -> None:
        try:
            if self._stream:
                self._stream.stop(); self._stream.close()
        except Exception:
            pass
        self._stream = None


class LiveSession:
    def __init__(self, mic_feed) -> None:
        self.mic_feed = mic_feed          # callable(chunk) from audio thread
        self.on_play = lambda b: None
        self.on_interrupt = lambda: None
        self.on_turn = lambda user, reply: None
        self.on_exit = lambda: None
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.audio_in: queue.Queue = queue.Queue(maxsize=200)
        from .barge_in import BargeInManager
        self.barge_in_mgr = BargeInManager(on_interrupt=lambda: self.on_interrupt())

    def start(self) -> bool:
        try:
            from google.genai import types  # noqa: F401
        except Exception as exc:
            self.last_error = f"google-genai missing: {exc}"
            return False
        if not settings.gemini_key:
            self.last_error = "no GEMINI_API_KEY"
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="mk2-live")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _pump_mic(self, session) -> None:
        async def sender():
            from google.genai import types

            while True:
                try:
                    chunk = await asyncio.to_thread(self.audio_in.get, True, 0.25)
                except Exception:
                    chunk = None
                if chunk is not None:
                    # Process frame for local barge-in trigger
                    self.barge_in_mgr.process_frame(chunk, is_playing=True)
                    blob = types.Blob(data=chunk, mime_type=f"audio/pcm;rate={INPUT_RATE}")
                    try:
                        await session.send_realtime_input(audio=blob)
                    except AttributeError:
                        await session.send(input=blob)
                else:
                    await asyncio.sleep(0.02)

        return asyncio.create_task(sender())

    def _run(self) -> None:
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        from google.genai import types

        client_mod = __import__("google.genai", fromlist=["genai"])
        client = client_mod.Client(api_key=settings.gemini_key)
        cfg = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=PERSONA,
            input_audio_transcription=types.AudioTranscription(),
            output_audio_transcription=types.AudioTranscription(),
        )
        session = None
        last_err = ""
        for model in settings.gemini_models or ["gemini-2.5-flash-native-audio-latest"]:
            try:
                cm = client.aio.live.connect(model=model, config=cfg)
                session = await cm.__aenter__()
                break
            except Exception as exc:
                last_err = f"{model}: {exc}"
        if session is None:
            raise RuntimeError(f"no live model: {last_err}")
        try:
            async with session:
                sender = self._pump_mic(session)
                try:
                    in_text = out_text = ""
                    async for msg in session.receive():
                        if self._stop.is_set():
                            break
                        sc = getattr(msg, "server_content", None)
                        if sc is not None and getattr(sc, "interrupted", False):
                            self.on_interrupt()
                        data = getattr(msg, "data", None)
                        if data:
                            self.on_play(data)
                        it = getattr(sc, "input_transcription", None) if sc else None
                        if it is not None and getattr(it, "text", ""):
                            in_text += it.text
                            if is_exit(in_text):
                                break
                        ot = getattr(sc, "output_transcription", None) if sc else None
                        if ot is not None and getattr(ot, "text", ""):
                            out_text += ot.text
                        if (sc is not None and getattr(sc, "turn_complete", False)):
                            if out_text.strip() or in_text.strip():
                                self.on_turn(in_text.strip(), out_text.strip())
                            in_text = out_text = ""
                finally:
                    sender.cancel()
        finally:
            self.on_exit()


# keep mic_feed wired: LiveSession exposes feed used by gateway
def _feed_shim(self, chunk):  # pragma: no cover
    self.audio_in.put_nowait(chunk)


LiveSession.feed = _feed_shim
