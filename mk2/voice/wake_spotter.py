"""On-device Keyword Spotter for EVO MK2.

Lightweight, low-CPU wake word detection:
1. openWakeWord ONNX model integration for zero-cloud keyword spotting.
2. Energy-based VAD pre-filtering (skips processing silent frames).
3. Graceful fallback to energy-VAD + Vosk phrase matching when OWW is uninstalled.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("mk2.voice.spotter")

ENERGY_THRESHOLD = 0.055  # RMS energy threshold for VAD pre-filtering (tuned to suppress background noise)


class WakeSpotter:
    def __init__(self, wake_phrases: Optional[list[str]] = None) -> None:
        self.wake_phrases = wake_phrases or [
            "evo", "hey evo", "ok evo", "eva", "eevo", "echo",
            "wake up evo", "woke up evo", "wake up ever",
            "jarvis", "hey jarvis", "ok jarvis",
        ]
        self.backend = "energy_vad_spotter"
        self._consecutive_silent_frames = 0
        self._noise_floor = 0.015
        self._oww_model = None

        try:
            from openwakeword.model import Model as OWWModel
            self._oww_model = OWWModel(
                wakeword_models=["hey_jarvis", "alexa"],
                inference_framework="onnx",
            )
            self.backend = "openwakeword"
            log.info("openWakeWord initialized successfully for on-device wake detection")
        except Exception as exc:
            log.debug("openWakeWord unavailable (%s) - running in energy-VAD mode", exc)
            self.backend = "energy_vad_spotter"

    @staticmethod
    def _rms(frame: bytes) -> float:
        import array
        if not frame:
            return 0.0
        try:
            a = array.array("h", frame)
            return (sum(s * s for s in a) / len(a)) ** 0.5 if a else 0.0
        except Exception:
            return 0.0

    def has_voice_energy(self, frame: bytes) -> bool:
        """Check if incoming frame contains voice energy above adaptive noise baseline."""
        rms = self._rms(frame) / 32768.0
        # Adaptive noise floor tracking (exponential moving average of background floor)
        self._noise_floor = 0.95 * self._noise_floor + 0.05 * min(rms, ENERGY_THRESHOLD)
        thresh = max(ENERGY_THRESHOLD, self._noise_floor * 1.8)
        if rms >= thresh:
            self._consecutive_silent_frames = 0
            return True
        self._consecutive_silent_frames = min(1000, self._consecutive_silent_frames + 1)
        return False

    def process_audio_frame(self, frame: bytes) -> bool:
        """Run keyword spotting on raw audio frame."""
        if not self.has_voice_energy(frame):
            return False

        if self._oww_model:
            try:
                import numpy as np
                samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
                preds = self._oww_model.predict(samples)
                if preds:
                    score = max(preds.values())
                    if score > 0.5:
                        return True
            except Exception:
                pass
        return False

    def match_transcript(self, text: str) -> Optional[str]:
        """Check if transcribed text contains any registered wake phrase (exact or embedded)."""
        t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()
        if not t:
            return None

        for phrase in self.wake_phrases:
            if t == phrase:
                return ""
            if t.startswith(phrase + " "):
                return t[len(phrase) + 1:].strip()
            # Support word boundary match anywhere in the transcription
            pattern = r"\b" + re.escape(phrase) + r"\b"
            m = re.search(pattern, t)
            if m:
                rest = t[m.end():].strip()
                return rest
        return None

    def reset(self) -> None:
        self._consecutive_silent_frames = 0
        self._noise_floor = 0.015
