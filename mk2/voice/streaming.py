"""Overlapped end-to-end voice streaming pipeline for sub-second latency.

Orchestrates:
Mic / STT partial -> Fast-path / Brain -> Streaming LLM -> Sentence-Chunked TTS -> Playback
with continuous overlapping and immediate barge-in interruption.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from .. import brain, llm
from .barge_in import BargeInManager
from .tts_stream import ChunkedSpeaker

log = logging.getLogger("mk2.voice.streaming")


class OverlappedVoicePipeline:
    """Zero-idle overlapped pipeline coordinating STT, LLM streaming, and chunked TTS."""

    def __init__(self, on_barge_in: Optional[Callable[[], None]] = None) -> None:
        self.chunked_speaker = ChunkedSpeaker()
        self.barge_in_mgr = BargeInManager(on_interrupt=self.interrupt)
        self.on_barge_in = on_barge_in
        self.is_active = False
        self._lock = threading.Lock()

    def interrupt(self) -> None:
        """Interrupt active pipeline playback immediately."""
        self.chunked_speaker.interrupt()
        self.is_active = False
        if self.on_barge_in:
            try:
                self.on_barge_in()
            except Exception as exc:
                log.warning("Pipeline barge-in callback note: %s", exc)

    def feed_mic_frame(self, frame: bytes) -> bool:
        """Process incoming mic audio during playback for instant interrupt."""
        return self.barge_in_mgr.process_frame(frame, is_playing=self.is_active)

    def process_utterance(self, text: str, surface: str = "voice") -> str:
        """Execute stream-overlapped voice turn."""
        t0 = time.time()
        self.is_active = True
        self.barge_in_mgr.reset()
        self.chunked_speaker.start()

        # Check sub-100ms fast path first
        fast = brain._fast_path(text) if hasattr(brain, "_fast_path") else None
        if fast:
            ttft = time.time() - t0
            log.info("Voice fast-path matched (TTFT=%.3fs): %s", ttft, fast[:40])
            self.chunked_speaker.feed(fast)
            self.chunked_speaker.finalize()
            return fast

        # Overlapped LLM streaming -> sentence chunking
        accumulated: list[str] = []
        try:
            # Build trimmed voice context (last 3 messages + current)
            msgs = [{"role": "system", "content": "You are EVO. Be concise, direct and natural. 1-3 sentences max."}]
            msgs.append({"role": "user", "content": text})

            stream_gen = llm.chat_stream(msgs, role="voice", timeout=15)
            first_token = True
            for delta in stream_gen:
                if first_token:
                    first_token = False
                    ttft = time.time() - t0
                    log.info("Voice stream TTFT: %.3fs", ttft)
                accumulated.append(delta)
                self.chunked_speaker.feed(delta)
        except Exception as exc:
            log.warning("Voice streaming LLM fallback: %s", exc)
            fallback = brain.handle_turn(text, surface=surface)
            self.chunked_speaker.feed(fallback)
            accumulated.append(fallback)
        finally:
            self.chunked_speaker.finalize()

        full_reply = "".join(accumulated).strip()
        return full_reply
