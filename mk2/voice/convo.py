"""Always-on conversation mode — open mic until you close it.

Start via POST /api/voice/convo {"on": true} (the console mic button).
While running: laptop mic streams continuously, your speech is
transcribed (Vosk + grammar rescue), short questions auto-finalize on
silence, replies go through the normal brain AND are spoken aloud.
Exit phrases: "stop listening" / "goodbye" / "close the mic".
"""
import queue
import threading
import time

import sounddevice as sd

from .. import brain
from ..bus import bus
from . import stt as stt_mod
from .tts import Speaker

RATE = 16000
FRAME = 1280


def _rms(frame: bytes) -> float:
    import array

    a = array.array("h", frame)
    return (sum(s * s for s in a) / len(a)) ** 0.5 if a else 0.0


def _should_finalize(kind: str, text: str, quiet: bool,
                     last_key: str, last_change: float, now: float) -> bool:
    """Silence-based endpointing for partials (mirrors voice gateway)."""
    if kind == "final":
        return True
    key = text.lower()[:80]
    return (kind == "partial" and quiet and key == last_key
            and now - last_change >= 0.8)


EXIT_PHRASES = ("stop listening", "close the mic", "close mic", "goodbye")


class ConversationMode:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.speaker = Speaker()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return False
        tts_cancel = getattr(self.speaker, "shut_up", None)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="mk2-convo")
        self._thread.start()
        bus.publish("system.voice", {"state": "convo"})
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            self.speaker.shut_up()
        except Exception:
            pass

    # ---------------------------------------------------------------- loop

    def _run(self) -> None:
        audio_q: "queue.Queue[bytes]" = queue.Queue(maxsize=400)
        dropped_frames = 0

        def cb(indata, frames, t_info, status):
            nonlocal dropped_frames
            try:
                audio_q.put_nowait(bytes(indata))
            except Exception:
                dropped_frames += 1
                if dropped_frames % 100 == 1:
                    log.warning("Convo audio queue full — %d frames dropped", dropped_frames)

        stream = None
        try:
            stream = sd.RawInputStream(
                samplerate=RATE, blocksize=FRAME, dtype="int16",
                channels=1, callback=cb)
            stream.start()
        except Exception as exc:
            bus.publish("notify.out", {"kind": "voice",
                                       "text": f"Mic unavailable: {exc}"})
            return

        s = stt_mod.Stream()
        if not s.ok:
            bus.publish("notify.out", {"kind": "voice",
                                       "text": "No STT model - convo off."})
            return

        self.speaker.say("Conversation mode active.")
        bus.publish("system.voice", {"state": "convo"})
        last_key = ""
        last_change = 0.0
        noise_floor = 300.0

        try:
            while not self._stop.is_set():
                try:
                    frame = audio_q.get(timeout=0.5)
                except Exception:
                    continue
                lvl = _rms(frame)
                noise_floor = 0.9 * noise_floor + 0.1 * lvl

                # Barge-in and acoustic echo suppression
                if self.speaker.is_speaking:
                    if lvl > max(650.0, noise_floor * 2.5):
                        self.speaker.shut_up()
                    else:
                        continue  # Echo suppression: do not process speaker playback

                kind, text = s.feed(frame)
                if not text:
                    continue
                now = time.time()
                quiet = lvl < max(320.0, noise_floor * 2.2)
                key = text.lower()[:80]
                if _should_finalize(kind, text, quiet, last_key,
                                    last_change, now):
                    last_key = ""
                    low = text.lower()
                    import re as _re
                    if any(_re.search(r"\b" + _re.escape(x) + r"\b", low) for x in EXIT_PHRASES):
                        self.speaker.say("Closing conversation mode.")
                        break

                    # Voiceprint verification (Issue #11)
                    try:
                        from .. import voiceprint
                        if voiceprint.is_enrolled():
                            vp_ok, score = voiceprint.verify_speaker(frame)
                            if not vp_ok:
                                log.warning("Convo turn rejected: voiceprint mismatch (score=%.2f)", score)
                                self.speaker.say("Voice not recognized.")
                                continue
                    except Exception:
                        pass

                    reply = brain.handle_turn(text, surface="voice")
                    self.speaker.say(reply)
                if key != last_key:
                    last_key = key
                    last_change = now
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
            bus.publish("system.voice", {"state": "idle"})


convo_mode = ConversationMode()


def start() -> bool:
    return convo_mode.start()


def stop() -> None:
    convo_mode.stop()


def status() -> dict:
    return {"running": convo_mode.running}
