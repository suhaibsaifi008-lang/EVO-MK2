"""Engine order: Piper (local neural JARVIS voice) first - free, fast
(RTF ~0.1 on CPU), no network. SAPI as instant local fallback (great for
very short lines), edge-tts only as last resort if Piper is unavailable.
EVO_TTS_ENGINE=sapi forces the old behavior; =piper / =edge pin engines."""
import os

from . import tts as _tts


def synthesize_best(text: str, force_engine: str | None = None):
    """force_engine='sapi' -> local Windows voice only (per-request escape
    hatch used by the API when neural throttles)."""
    text = " ".join((text or "").split())[:600]
    mode = os.environ.get("EVO_TTS_ENGINE", "auto").lower()

    if mode == "sapi" or force_engine == "sapi":
        return _tts._sapi_wav(text)

    # 1) piper: local ONNX JARVIS voice (~0.1-0.4s per sentence on CPU)
    if force_engine in ("piper", None) and mode in ("auto", "piper"):
        try:
            p = _tts._piper_wav(text)
            if p:
                return p
        except Exception:
            pass

    # 2) sapi: instant offline Windows voice
    p = _tts._sapi_wav(text)
    if p:
        return p

    # 3) edge-tts: network last resort (only when piper AND sapi failed)
    try:
        import threading

        return _tts._edge_mp3(text, threading.Event())
    except Exception:
        return None
