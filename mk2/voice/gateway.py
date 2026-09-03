"""Voice gateway: SLEEPING -> (LIVE|LOCAL) session state machine.

SLEEPING  : Vosk partials watched for the wake phrase.
LIVE      : mic streams to Gemini Live; local grammar sniffer executes instant
            commands; exit phrase / idle / error returns to SLEEPING.
LOCAL     : same conversation loop but fully offline (early-commit endpointing,
            grammar-rescued commands, hybrid TTS).
"""
import json
import queue
import logging
import threading
import time

import sounddevice as sd

from .. import brain
from ..bus import bus
from . import live as live_mod
from . import stt as stt_mod
from . import wake as wake_mod
from .tts import Speaker

log = logging.getLogger("mk2.voice")

SAMPLE_RATE = 16000
FRAME = 1280  # 80ms int16 mono
IDLE_EXIT = 45.0


class Gateway:
    def __init__(self) -> None:
        self.state = "off"
        self.engine = "local"
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self.speaker = Speaker()
        self.audio_q: "queue.Queue[bytes]" = queue.Queue(maxsize=300)
        self.stream = None

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="mk2-voice")
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()

    def status(self) -> dict:
        return {
            "engine": ("gemini-live" if live_mod.available() else "local"),
            "state": self.state,
            "running": bool(self._thread and self._thread.is_alive()),
        }

    # ---------------- audio ----------------

    _dropped_frames: int = 0

    def _callback(self, indata, frames, tinfo, status) -> None:
        try:
            self.audio_q.put_nowait(bytes(indata))
        except Exception:
            self._dropped_frames += 1
            if self._dropped_frames % 100 == 1:
                log.warning("Audio queue full — %d frames dropped so far", self._dropped_frames)

    @staticmethod
    def _rms(frame: bytes) -> float:
        import array

        a = array.array("h", frame)
        return (sum(s * s for s in a) / len(a)) ** 0.5 if a else 0.0

    def _drain(self) -> None:
        try:
            while True:
                self.audio_q.get_nowait()
        except Exception:
            pass

    # ---------------- main loop ----------------

    def _run(self) -> None:
        stream = stt_mod.Stream()
        if not stream.ok:
            log.warning("voice disabled: no STT model")
            self.state = "no-model"
            bus.publish("system.voice", {"state": self.state})
            return
        self.state = "sleeping"
        bus.publish("system.voice", {"state": self.state})
        try:
            self.stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=FRAME, dtype="int16",
                channels=1, callback=self._callback)
            self.stream.start()
        except Exception as exc:
            log.error("mic open failed: %s", exc)
            self.state = "mic-error"
            return

        last_partial_key = ""
        last_partial_change = 0.0
        noise_floor = 300.0
        log.info("voice gateway sleeping - say '%s'", "/".join(wake_mod.WAKE_PHRASES))

        spotter = wake_mod.get_spotter()
        while not self._stop_evt.is_set():
            try:
                frame = self.audio_q.get(timeout=1.0)
            except Exception:
                continue

            # Echo suppression: skip processing speaker feedback
            if self.speaker.is_speaking:
                if self._rms(frame) > max(650.0, noise_floor * 2.5):
                    self.barge_in_local()
                continue

            # Phase 2: Energy VAD pre-filter to save CPU
            if not spotter.has_voice_energy(frame) and self.state == "sleeping":
                continue

            kind, text = stream.feed(frame)
            if not text:
                continue
            if self.state == "sleeping":
                rest = wake_mod.match_wake(text)
                if rest is None:
                    continue
                print("[voice] wake:", text[:60], flush=True)
                self._chime()
                self._drain()
                engine = "live" if live_mod.available() else "local"
                self.engine = engine
                if rest.strip() and not wake_mod.is_exit(rest):
                    # command rode the wake breath: run locally first
                    self._run_local_command(rest)
                bus.publish("system.voice", {"state": "session", "engine": engine})
                if engine == "live":
                    ok = self._live_session(stream)
                else:
                    ok = self._local_session(stream)
                self.state = "sleeping"
                bus.publish("system.voice", {"state": self.state})
                last_partial_key = ""
            elif self.state == "session":
                pass  # handled inside session loops

        if self.stream:
            try:
                self.stream.stop()
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:
                pass

    # ---------------- sessions ----------------

    def _live_session(self, stream) -> bool:
        sess = live_mod.LiveSession(self.audio_q.put_nowait)
        speaker = live_mod.Speaker()
        sess.on_play = speaker.play
        sess.on_interrupt = speaker.interrupt
        ended = threading.Event()
        self.state = "session"

        def on_turn(user: str, reply: str) -> None:
            if user or reply:
                bus.publish("voice.turn", {"user": user[:400], "reply": reply[:900]})
                print(f"[voice/live] {user[:60]!r} -> {len(reply)} chars", flush=True)

        sess.on_turn = on_turn
        sess.on_exit = ended.set
        if not sess.start():
            log.warning("live unavailable (%s) -> local session", sess.last_error)
            return False
        speaker.start()
        self._drain()
        grec_cmd = ""
        last_voice = time.time()
        try:
            while not ended.is_set() and not sess.stopped and not self._stop_evt.is_set():
                try:
                    frame = self.audio_q.get(timeout=1.0)
                except Exception:
                    if time.time() - last_voice > IDLE_EXIT:
                        break
                    continue
                sess.feed(frame)
                if self._rms(frame) > 500:
                    last_voice = time.time()
                kind, text = stream.feed(frame)
                if not text:
                    continue
                if kind == "final" or wake_mod.is_exit(text):
                    grec_cmd = text
                if wake_mod.is_exit(grec_cmd or text):
                    break
                if kind == "final" and _is_instant_command(text):
                    speaker.interrupt()
                    self.speaker.shut_up()
                    self._run_local_command(text)
                    last_voice = time.time()
        finally:
            sess.stop()
            ended.wait(timeout=8)
            speaker.interrupt(); speaker.stop()
            self.barge_in_local()
        return True

    def _local_session(self, stream) -> bool:
        self.state = "session"
        self.say_local("I'm listening.")
        last_partial_key = ""
        last_partial_change = 0.0
        last_activity = time.time()
        noise_floor = 300.0
        while not self._stop_evt.is_set():
            try:
                frame = self.audio_q.get(timeout=1.0)
            except Exception:
                if time.time() - last_activity > IDLE_EXIT:
                    self.say_local("Going back to standby.")
                    return True
                continue
            lvl = self._rms(frame)
            # Barge-in and acoustic echo suppression
            if self.speaker.is_speaking:
                if lvl > max(650.0, noise_floor * 2.5):
                    self.barge_in_local()
                else:
                    # Echo suppression: do not feed speaker output back into STT engine
                    continue

            kind, text = stream.feed(frame)
            if not text:
                continue
            key = text.lower()[:80]
            quiet = lvl < max(320.0, noise_floor * 2.2)
            if kind == "partial" and quiet and key == last_partial_key \
                    and time.time() - last_partial_change >= 0.7:
                kind = "final"
            if key != last_partial_key:
                last_partial_key = key
                last_partial_change = time.time()
            if kind != "final":
                continue
            last_activity = time.time()
            last_partial_key = ""
            if wake_mod.is_exit(text):
                self.say_local("Very good. Say the wake phrase when you need me.")
                return True
            self._run_local_command(text)
        return True

    # ---------------- helpers ----------------

    def _run_local_command(self, text: str) -> None:
        """Deterministic/agent execution with spoken result (worker thread)."""
        # Security Gate: verify voiceprint if enrolled and security active
        try:
            from ..security import voiceprint
            if voiceprint.is_enrolled() and voiceprint.is_locked_out():
                remaining = voiceprint.get_lockout_seconds_remaining()
                self.say_local(f"Voice security locked. Try again in {remaining} seconds or verify PIN.")
                return
        except Exception:
            pass

        # Voice Fast-Path: evaluate instant/offline commands in <100ms without LLM
        try:
            from ..fastlane import fast_command
            from ..llm import _offline_parse
            from ..brain import _fast_path

            instant = fast_command(text, surface="voice") or _fast_path(text) or _offline_parse(text)
            if instant is not None:
                self.say_local(instant)
                bus.publish("voice.command", {"text": text, "reply": instant})
                return
        except Exception:
            pass

        def worker() -> None:
            try:
                from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(brain.handle_turn, text, surface="voice")
                    try:
                        reply = future.result(timeout=45)
                    except FuturesTimeout:
                        reply = "I'm sorry, that request took too long. Please try again."
                        log.warning("Voice command timed out after 45s: %s", text[:60])
                self.say_local(reply)
                bus.publish("voice.command", {"text": text, "reply": reply})
            except Exception as exc:
                log.warning("command failed: %s", exc)

        threading.Thread(target=worker, daemon=True, name="mk2-cmd").start()

    def say_local(self, text: str) -> None:
        self.speaker.shut_up()
        self.speaker.say(text)

    def barge_in_local(self) -> None:
        self.speaker.shut_up()

    def _chime(self) -> None:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass



def _is_instant_command(text: str) -> bool:
    t = text.lower().strip()
    return t.startswith(("open ", "close ", "volume ", "play ")) and len(t.split()) <= 6


gateway = Gateway()


def status() -> dict:
    return gateway.status()




def run_async():  # legacy name kept for compatibility
    gateway.start()

