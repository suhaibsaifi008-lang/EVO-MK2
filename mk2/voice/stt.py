"""Local STT: realtime Vosk with grammar rescue."""
import json
import os
import threading
import urllib.request
import zipfile
from pathlib import Path

from ..config import DATA

MODELS_DIR = DATA / "models"
VOSK_DIR = MODELS_DIR / "vosk"
URLS = [
    "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
]
SAMPLE_RATE = 16000
_lock = threading.Lock()
_model = None


def ensure_model() -> Path | None:
    if VOSK_DIR.exists() and any(VOSK_DIR.iterdir()):
        return VOSK_DIR
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    zipped = MODELS_DIR / "vosk.zip"
    for url in URLS:
        try:
            print("[stt] downloading", url.rsplit("/", 1)[-1], flush=True)
            urllib.request.urlretrieve(url, zipped)
            with zipfile.ZipFile(zipped) as zf:
                inner = zf.namelist()[0].split("/")[0]
                zf.extractall(MODELS_DIR)
            src = MODELS_DIR / inner
            if VOSK_DIR.exists():
                for c in src.iterdir():
                    c.replace(VOSK_DIR / c.name)
                src.rmdir()
            else:
                src.rename(VOSK_DIR)
            zipped.unlink(missing_ok=True)
            if any(VOSK_DIR.iterdir()):
                return VOSK_DIR
        except Exception as exc:
            print("[stt] download failed:", exc, flush=True)
    return None


def get_model():
    global _model
    with _lock:
        if _model is not None:
            return _model
        import vosk

        vosk.SetLogLevel(-1)
        d = ensure_model()
        if not d:
            return None
        _model = vosk.Model(str(d))
        return _model


class Stream:
    """General + strict-grammar recognizers in one feed."""

    def __init__(self) -> None:
        model = get_model()
        self.ok = model is not None
        if not self.ok:
            return
        from vosk import KaldiRecognizer

        from .grammar_rescue import grammar_json
        self.gen = KaldiRecognizer(model, SAMPLE_RATE)
        self.gen.SetWords(False)
        try:
            self.cmd = KaldiRecognizer(model, SAMPLE_RATE, grammar_json())
            self.cmd.SetWords(False)
        except Exception:
            self.cmd = None

    def feed(self, chunk: bytes) -> tuple[str, str]:
        """Returns (kind, text): kind in partial|final|'' (no event)."""
        if not self.ok:
            return "", ""
        out_final = ""
        try:
            is_final = self.gen.AcceptWaveform(chunk)
        except Exception:
            return "", ""
        heard = ""
        if is_final:
            try:
                heard = json.loads(self.gen.FinalResult()).get("text", "")
            except Exception:
                heard = ""
            kind = "final"
        else:
            try:
                heard = json.loads(self.gen.PartialResult()).get("partial", "")
            except Exception:
                heard = ""
            kind = "partial" if heard else ""
        gtext = ""
        if self.cmd is not None and kind:
            try:
                gf = self.cmd.AcceptWaveform(chunk)
                gtext = json.loads(
                    self.cmd.FinalResult() if gf else self.cmd.PartialResult()
                ).get("text", "")
            except Exception:
                pass
        from .grammar_rescue import trust_grammar
        if gtext and trust_grammar(gtext, heard):
            heard, kind = gtext, "final"  # trusted command phrasing wins
        return (kind, heard.strip()) if kind and heard.strip() else ("", "")
