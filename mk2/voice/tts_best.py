"""Pick engine per text: instant local SAPI for short, neural for long."""
import os

from . import tts as _tts


def synthesize_best(text: str):
    text = " ".join((text or "").split())[:600]
    mode = os.environ.get("EVO_TTS_ENGINE", "auto").lower()
    if mode == "sapi":
        return _tts._sapi_wav(text)
    if len(text) <= 90:
        p = _tts._sapi_wav(text)
        if p:
            return p
    return _tts._edge_mp3(text, threading.Event())
