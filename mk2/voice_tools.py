"""Voice selection tools — switch EVO's spoken voice without editing code."""
import os

from .tools import tool
from .voice import tts as _tts


@tool("tts_voices", "List available voices: Windows SAPI (installed) + curated neural options.",
      {}, permission="read")
def tts_voices() -> dict:
    sapi = _tts.list_sapi_voices()
    current_edge = _tts._edge_voice()
    current_sapi = os.environ.get("EVO_SAPI_VOICE", "(system default)")
    speech = (f"Neural: set EVO_TTS_VOICE to one of "
              f"{', '.join(_tts.CURATED_VOICES[:5])}... "
              f"(current: {current_edge}). Windows voices: "
              + (", ".join(sapi[:6]) or "none found")
              + f" (current filter: {current_sapi}).")
    return {"ok": True, "speech": speech,
            "data": {"neural_options": _tts.CURATED_VOICES,
                     "current_neural": current_edge,
                     "windows_voices": sapi,
                     "current_sapi_filter": current_sapi}}


@tool("tts_set_voice", "Switch EVO's speaking voice. Pass a neural name (e.g. en-IN-NeerjaNeural) or a Windows voice substring (e.g. Zira). Empty string resets.",
      {"voice": {"type": "string"}}, permission="execute")
def tts_set_voice(voice: str) -> dict:
    voice = (voice or "").strip()
    neural_names = {v.lower() for v in _tts.CURATED_VOICES}
    if not voice:
        os.environ.pop("EVO_TTS_VOICE", None)
        os.environ.pop("EVO_SAPI_VOICE", None)
        return {"ok": True, "speech": "Voice reset to defaults.", "data": {}}
    if voice.lower() in neural_names or "neural" in voice.lower():
        os.environ["EVO_TTS_VOICE"] = voice
        return {"ok": True,
                "speech": f"Neural voice set to {voice}. Applies from the next reply.",
                "data": {"EVO_TTS_VOICE": voice}}
    # treat as a Windows SAPI voice substring
    matches = [v for v in _tts.list_sapi_voices() if voice.lower() in v.lower()]
    if not matches:
        return {"ok": False,
                "speech": (f"No Windows voice matching '{voice}'. Try "
                           "tts_voices to see the list."),
                "data": {}}
    os.environ["EVO_SAPI_VOICE"] = matches[0]
    return {"ok": True,
            "speech": f"Windows voice set to {matches[0]}.",
            "data": {"EVO_SAPI_VOICE": matches[0]}}
