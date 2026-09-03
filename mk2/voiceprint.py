"""Voiceprint feature extraction, enrollment, and verification for EVO MK2.

Provides biometric voice authentication, lockout management, and fallback PIN verification.
"""
import hashlib
import json
import logging
import os
import time
from typing import Optional

import numpy as np

from . import config

log = logging.getLogger("mk2.security.voiceprint")

VOICEPRINT_FILE = config.DATA / "vault" / "voiceprint.json"
_lockout_until = 0.0
_consecutive_fails = 0


def _extract_spectral_features(audio_bytes: bytes, sample_rate: int = 16000) -> np.ndarray:
    """Extract a 32-dimensional spectral signature (energy, centroid, band energies)."""
    if not audio_bytes:
        return np.zeros(32, dtype=np.float32)
    # Convert PCM16 bytes to float numpy array
    pcm = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if len(pcm) < 512:
        pcm = np.pad(pcm, (0, max(0, 512 - len(pcm))))

    # Compute magnitude spectrum via FFT
    windowed = pcm * np.hanning(len(pcm))
    fft_mag = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(len(pcm), d=1.0 / sample_rate)

    # 1. Total RMS Energy
    rms = np.sqrt(np.mean(pcm ** 2))
    # 2. Spectral Centroid
    centroid = np.sum(freqs * fft_mag) / (np.sum(fft_mag) + 1e-9)
    # 3. Spectral Spread / Bandwidth
    spread = np.sqrt(np.sum(((freqs - centroid) ** 2) * fft_mag) / (np.sum(fft_mag) + 1e-9))

    # 4. 28-band filterbank energies
    bands = np.array_split(fft_mag, 28)
    band_energies = [np.mean(b) if len(b) > 0 else 0.0 for b in bands]

    features = np.array([rms, centroid / 4000.0, spread / 2000.0] + band_energies[:29], dtype=np.float32)
    if len(features) < 32:
        features = np.pad(features, (0, 32 - len(features)))
    else:
        features = features[:32]

    # Normalize vector to unit length
    norm = np.linalg.norm(features)
    return features / norm if norm > 1e-9 else features


def enroll_voiceprint(audio_bytes: bytes) -> dict:
    """Enroll the primary user's voiceprint."""
    features = _extract_spectral_features(audio_bytes)
    profile = {
        "enrolled_at": time.time(),
        "vector": features.tolist(),
        "sample_bytes": len(audio_bytes),
        "hash": hashlib.sha256(features.tobytes()).hexdigest()[:16],
    }
    VOICEPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    VOICEPRINT_FILE.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    log.info("Primary user voiceprint enrolled successfully (%d bytes sample)", len(audio_bytes))
    return {"ok": True, "speech": "Voiceprint enrolled. Voice biometric security active.", "data": {"hash": profile["hash"]}}


def is_enrolled() -> bool:
    return VOICEPRINT_FILE.exists() or os.environ.get("EVO_VOICEPRINT_ENROLLED", "0") == "1"


def is_locked_out() -> bool:
    global _lockout_until
    return time.time() < _lockout_until


def get_lockout_seconds_remaining() -> int:
    global _lockout_until
    return max(0, int(_lockout_until - time.time()))


def verify_voiceprint(audio_bytes: bytes, threshold: float = 0.70) -> dict:
    """Compare inbound audio against the enrolled voiceprint profile."""
    global _consecutive_fails, _lockout_until

    if not is_enrolled():
        return {"ok": True, "confidence": 1.0, "reason": "unenrolled"}

    if is_locked_out():
        return {
            "ok": False,
            "confidence": 0.0,
            "error": "locked_out",
            "cooldown_remaining_s": get_lockout_seconds_remaining(),
            "speech": f"Voice security locked. Try again in {get_lockout_seconds_remaining()} seconds or use PIN.",
        }

    try:
        profile = json.loads(VOICEPRINT_FILE.read_text(encoding="utf-8"))
        stored_vec = np.array(profile["vector"], dtype=np.float32)
    except Exception as exc:
        log.error("Failed to read voiceprint profile: %s", exc)
        return {"ok": False, "confidence": 0.0, "error": "profile_read_error", "speech": "Voice security profile read error."}

    inbound_vec = _extract_spectral_features(audio_bytes)
    similarity = float(np.dot(stored_vec, inbound_vec))

    if similarity >= threshold:
        _consecutive_fails = 0
        return {"ok": True, "confidence": round(similarity, 3)}
    else:
        _consecutive_fails += 1
        log.warning("Voiceprint mismatch: similarity %.3f below threshold %.3f (fails: %d/3)", similarity, threshold, _consecutive_fails)
        if _consecutive_fails >= 3:
            _lockout_until = time.time() + 60.0
            log.error("Voice security threshold exceeded: locking out voice commands for 60s")
            return {
                "ok": False,
                "confidence": round(similarity, 3),
                "error": "locked_out",
                "cooldown_remaining_s": 60,
                "speech": "Voice verification failed 3 times. Voice input locked for 60 seconds. Please enter your PIN.",
            }
        return {
            "ok": False,
            "confidence": round(similarity, 3),
            "error": "voice_mismatch",
            "speech": "Voice print not recognized. Access denied.",
        }


def verify_pin(pin: str) -> bool:
    """Verify fallback security PIN."""
    global _consecutive_fails, _lockout_until
    import hmac
    env_pin = os.environ.get("EVO_PIN", "").strip()
    if not env_pin:
        return False
    target_hash = hashlib.sha256(env_pin.encode()).hexdigest()
    in_hash = hashlib.sha256(str(pin).strip().encode()).hexdigest()
    if hmac.compare_digest(in_hash, target_hash):
        _consecutive_fails = 0
        _lockout_until = 0.0
        log.info("Security lockout cleared via valid PIN")
        return True
    return False
