"""Pick engine per text: neural (edge-tts) first for quality, SAPI local
as instant/offline fallback. EVO_TTS_ENGINE=sapi forces the old behavior."""
import os
import threading

from . import tts as _tts


def synthesize_best(text: str, force_engine: str | None = None):
    """force_engine='sapi' -> local Windows voice only (per-request escape
    hatch used by the API when neural throttles)."""
    text = " ".join((text or "").split())[:600]
    mode = os.environ.get("EVO_TTS_ENGINE", "auto").lower()

    if mode == "sapi" or force_engine == "sapi":
        return _tts._sapi_wav(text)

    # neural first: natural voice, ~1-2s synth (cached by content hash)
    try:
        p = _tts._edge_mp3(text, threading.Event())
        if p:
            return p
    except Exception:
        pass

    # offline / edge-tts down fallback
    p = _tts._sapi_wav(text)
    return p
