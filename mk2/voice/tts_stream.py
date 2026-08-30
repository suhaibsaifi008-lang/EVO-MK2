"""Chunked / Streaming TTS for EVO MK2.

Pipes streaming LLM token deltas to Piper / audio player sentence-by-sentence
in real time. First audio starts within 1-2s of generation, eliminating silence.
Supports immediate barge-in interruption.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Callable, Generator, Optional

from .tts import Speaker

log = logging.getLogger("mk2.voice.tts_stream")

CHUNK_SIZE = 80   # max chars before sending to keep audio latency <800ms
MIN_CHUNK = 30    # minimum chars before whitespace split


class ChunkedSpeaker:
    def __init__(self, speaker: Optional[Speaker] = None) -> None:
        self.speaker = speaker or Speaker()
        self._queue: queue.Queue[Optional[str]] = queue.Queue(maxsize=50)
        self._buffer: str = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_speaking = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start async speaker worker thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True, name="evo-tts-stream")
        self._thread.start()

    def feed(self, delta: str) -> None:
        """Feed text delta token from LLM stream with sub-second chunk emission."""
        if self._stop_event.is_set() or not delta:
            return

        self._buffer += delta

        # Check for sentence boundaries
        parts = _SENTENCE_BOUNDARY.split(self._buffer)
        if len(parts) > 1:
            # Everything except the last part is a completed sentence
            for sentence in parts[:-1]:
                s = sentence.strip()
                if s and len(s) >= 2:
                    try:
                        self._queue.put_nowait(s)
                    except queue.Full:
                        pass
            self._buffer = parts[-1]
        elif len(self._buffer) >= CHUNK_SIZE:
            # Low-latency optimization: if buffer exceeds CHUNK_SIZE, split on last whitespace
            last_space = self._buffer.rfind(" ")
            if last_space >= MIN_CHUNK:
                s = self._buffer[:last_space].strip()
                if s:
                    try:
                        self._queue.put_nowait(s)
                    except queue.Full:
                        pass
                self._buffer = self._buffer[last_space:].lstrip()

    def finalize(self) -> None:
        """Flush remaining buffer and mark stream end."""
        rem = self._buffer.strip()
        if rem and len(rem) >= 2 and not self._stop_event.is_set():
            try:
                self._queue.put_nowait(rem)
            except queue.Full:
                pass
        self._buffer = ""
        try:
            self._queue.put_nowait(None)  # Sentinel to finish
        except queue.Full:
            pass

    def interrupt(self) -> None:
        """Immediate barge-in: stop playback and clear pending queues."""
        self._stop_event.set()
        with self._lock:
            self._buffer = ""
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except Exception:
                    break
        try:
            self.speaker.stop()
        except Exception:
            pass
        log.info("Streaming TTS interrupted by barge-in")

    def wait_done(self, timeout: float = 15.0) -> None:
        """Wait for audio queue to finish playing."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self._queue.empty() and not self._is_speaking:
                break
            time.sleep(0.05)

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if chunk is None:  # Sentinel
                break

            if self._stop_event.is_set():
                break

            self._is_speaking = True
            try:
                self.speaker.speak(chunk)
            except Exception as exc:
                log.debug("TTS chunk speak error: %s", exc)
            finally:
                self._is_speaking = False
                self._queue.task_done()
