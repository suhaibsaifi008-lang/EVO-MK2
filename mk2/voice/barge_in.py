"""Unified Barge-In Manager for real-time speech interrupt during TTS playback.

Detects when a user begins speaking while audio is actively playing,
immediately invoking interruption callbacks in under 100ms.
"""
from __future__ import annotations

import array
import logging
from typing import Callable, Optional

log = logging.getLogger("mk2.voice.barge_in")


class BargeInManager:
    """Detects user speech during TTS playback and triggers instant interruption."""

    def __init__(
        self,
        on_interrupt: Optional[Callable[[], None]] = None,
        threshold: float = 0.03,  # normalized float [0.0, 1.0] (approx 983 / 32768)
        speech_frames_required: int = 3,
        silence_frames_reset: int = 10,
    ) -> None:
        self.on_interrupt = on_interrupt
        self.threshold = threshold
        self.speech_frames_required = speech_frames_required
        self.silence_frames_reset = silence_frames_reset
        self.speech_count = 0
        self.silence_count = 0
        self.is_playing = False
        self.interrupted = False

    def set_playing(self, playing: bool) -> None:
        self.is_playing = playing
        if not playing:
            self.speech_count = 0
            self.silence_count = 0
            self.interrupted = False

    def compute_rms(self, frame: bytes) -> float:
        """Compute normalized RMS energy in range [0.0, 1.0] from 16-bit PCM bytes."""
        if not frame:
            return 0.0
        try:
            a = array.array("h", frame)
            if not a:
                return 0.0
            sum_sq = sum(s * s for s in a)
            rms = (sum_sq / len(a)) ** 0.5
            return rms / 32768.0
        except Exception:
            return 0.0

    def process_frame(self, frame: bytes, is_playing: Optional[bool] = None) -> bool:
        """Process an audio frame. Returns True if barge-in was triggered on this frame."""
        if is_playing is not None:
            self.is_playing = is_playing

        if not self.is_playing:
            self.speech_count = 0
            self.silence_count = 0
            return False

        rms = self.compute_rms(frame)
        if rms >= self.threshold:
            self.speech_count += 1
            self.silence_count = 0
            if self.speech_count >= self.speech_frames_required and not self.interrupted:
                self.interrupted = True
                log.info("Barge-in triggered (RMS=%.3f, frames=%d)", rms, self.speech_count)
                if self.on_interrupt:
                    try:
                        self.on_interrupt()
                    except Exception as exc:
                        log.warning("Barge-in callback failed: %s", exc)
                return True
        else:
            self.silence_count += 1
            if self.silence_count >= self.silence_frames_reset:
                self.speech_count = 0
                self.interrupted = False

        return False

    def reset(self) -> None:
        self.speech_count = 0
        self.silence_count = 0
        self.interrupted = False
