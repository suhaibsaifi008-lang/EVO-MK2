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

ENERGY_THRESHOLD = 0.035  # RMS energy threshold for VAD pre-filtering


class WakeSpotter:
    def __init__(self, wake_phrases: Optional[list[str]] = None) -> None:
        self.wake_phrases = wake_phrases or [
            "evo", "hey evo", "ok evo", "wake up evo", "woke up evo", "wake up ever",
            "jarvis", "hey jarvis", "ok jarvis",
        ]
        self.backend = "energy_vad_spotter"
        self._consecutive_silent_frames = 0
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
        """Check if incoming frame contains voice energy above silence baseline."""
        rms = self._rms(frame)
        if rms >= ENERGY_THRESHOLD:
            self._consecutive_silent_frames = 0
            return True
        self._consecutive_silent_frames += 1
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
        """Check if transcribed text contains any registered wake phrase."""
        t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()
        if not t:
            return None

        for phrase in self.wake_phrases:
            if t == phrase:
                return ""
            if t.startswith(phrase + " "):
                rest = t[len(phrase) + 1:].strip()
                if len(rest) >= 2:
                    return rest
        return None

    def reset(self) -> None:
        self._consecutive_silent_frames = 0
