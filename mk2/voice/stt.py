"""Local + cloud STT: realtime Vosk stream + two-grade PTT transcription.

PTT entrypoint engine chain (EVO_STT_ENGINE):
    auto (default) -> 1. Gemini audio (top-tier accuracy, needs internet)
                       2. faster-whisper small.en (offline fallback)
                       3. vosk (last resort)
    whisper | vosk force a single engine.

Models/config:
  EVO_STT_MODEL=small.en     whisper size (tiny.en/base.en/small.en/...)
  EVO_GEMINI_STT_MODEL       gemini model used for transcription
"""
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

_wsp = {"model": None, "name": ""}
_wsp_lock = threading.Lock()


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


def _resample(pcm: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE) -> bytes:
    """Linear-interpolation resample of 16-bit PCM (browser mics often
    deliver 48 kHz; the vosk model is 16 kHz - feeding raw 48k wrecks
    accuracy)."""
    import array

    if src_rate == dst_rate:
        return pcm
    samps = array.array("h", pcm)
    if not samps:
        return pcm
    ratio = dst_rate / src_rate
    n_out = max(1, int(len(samps) * ratio))
    out = array.array("h", bytes(2 * n_out))
    last = len(samps) - 1
    for i in range(n_out):
        pos = i / ratio
        i0 = int(pos)
        i1 = i0 + 1 if i0 < last else last
        frac = pos - i0
        out[i] = int(samps[i0] * (1 - frac) + samps[i1] * frac)
    return out.tobytes()


def _whisper_model():
    """Lazy-load + cache the faster-whisper model. First call downloads it."""
    name = os.environ.get("EVO_STT_MODEL", "small.en")
    with _wsp_lock:
        if _wsp["model"] is not None and _wsp["name"] == name:
            return _wsp["model"]
        from faster_whisper import WhisperModel

        print(f"[stt] loading whisper model '{name}' "
              "(first time downloads from HuggingFace)...", flush=True)
        m = WhisperModel(name, device="cpu", compute_type="int8")
        _wsp["model"], _wsp["name"] = m, name
        return m


def _transcribe_whisper(pcm: bytes) -> str:
    import numpy as np

    audio = np.frombuffer(pcm, dtype=np.int16).astype("float32") / 32768.0
    model = _whisper_model()
    segments, _info = model.transcribe(
        audio, language="en", beam_size=5, temperature=0.0,
        condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segments).strip()


def _wrap_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    import struct

    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
              + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(pcm)))
    return header + pcm


def _bounded(fn, seconds: float):
    """Wall-clock limit for SDK calls that lack clean timeouts."""
    box: dict = {}

    def runner():
        try:
            box["r"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["e"] = exc
    th = threading.Thread(target=runner, daemon=True)
    th.start()
    th.join(seconds)
    if "r" not in box:
        raise TimeoutError(f"gemini stt exceeded {seconds:.0f}s")
    if "e" in box:
        raise box["e"]
    return box["r"]


def _transcribe_gemini(pcm: bytes) -> str:
    """Top-tier cloud transcription via the Gemini key you already have."""
    def run() -> str:
        import os

        from google import genai
        from google.genai import types

        model = os.environ.get("EVO_GEMINI_STT_MODEL",
                               "gemini-3.6-flash").strip()
        client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY")
            or os.environ.get("JARVIS_GEMINI_KEY", ""))
        wav = _wrap_wav(pcm)
        resp = client.models.generate_content(
            model=model,
            contents=["Transcribe this audio exactly. Output ONLY the "
                      "transcribed words, nothing else.",
                      types.Part.from_bytes(data=wav, mime_type="audio/wav")],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (resp.text or "").strip()

    return _bounded(run, 20)


def transcribe_wav(data: bytes) -> str:
    """PTT entrypoint. Engine chain per EVO_STT_ENGINE (default auto):
    whisper (1.3s local, proven accurate) -> gemini (top-tier ~4s) -> vosk.
    Set EVO_STT_ENGINE=gemini to force cloud-only."""
    engine = os.environ.get("EVO_STT_ENGINE", "auto").lower().strip()
    pcm, rate = decode_wav(data)

    if engine == "whisper":
        text = _transcribe_whisper(pcm)
        if text:
            return text
        return _transcribe_vosk(pcm)
    if engine == "gemini":
        return _transcribe_gemini(pcm) or _transcribe_vosk(pcm)
    if engine == "vosk":
        return _transcribe_vosk(pcm)

    # auto: whisper first (fast + offline). Gemini only rescues an ENGINE
    # failure - never silence (gemini hallucinates words on noise).
    try:
        text = _transcribe_whisper(pcm)
        if text:
            return text
        return ""                       # genuine silence -> say nothing
    except Exception:
        pass                            # engine failure -> try gemini below
    try:
        text = _transcribe_gemini(pcm)
        if text:
            return text
    except Exception:
        pass
    return _transcribe_vosk(pcm)


def decode_wav(data: bytes) -> tuple[bytes, int]:
    """Parse browser WAV -> mono 16-bit PCM at 16 kHz. Pure parsing."""
    import struct

    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos = 12
    rate = SAMPLE_RATE
    channels = 1
    pcm = b""
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        (chunk_size,) = struct.unpack("<I", data[pos + 4:pos + 8])
        body = data[pos + 8:pos + 8 + chunk_size]
        if chunk_id == b"fmt ":
            _fmt, ch, rt = struct.unpack("<HHI", body[:8])
            channels = ch
            rate = rt
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + chunk_size + (chunk_size & 1)
    if not pcm:
        raise ValueError("no audio data")

    if channels > 1:
        import array
        samps = array.array("h", pcm)
        mono = array.array("h", (samps[i] for i in range(0, len(samps), channels)))
        pcm = mono.tobytes()

    if rate != SAMPLE_RATE:
        pcm = _resample(pcm, rate)
        rate = SAMPLE_RATE
    return pcm, SAMPLE_RATE


def _transcribe_vosk(pcm: bytes) -> str:
    model = get_model()
    if not model:
        raise RuntimeError("Vosk model not loaded")
    from vosk import KaldiRecognizer

    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(False)
    frame_bytes = SAMPLE_RATE * 2 // 10
    for i in range(0, len(pcm), frame_bytes):
        piece = pcm[i:i + frame_bytes]
        if piece:
            rec.AcceptWaveform(piece)

    text = json.loads(rec.FinalResult()).get("text", "").strip()
    try:
        from .grammar_rescue import grammar_phrases
        from difflib import get_close_matches

        words = text.lower().split()
        vocab = sorted({p.split()[-1] for p in grammar_phrases() if " " in p})
        for i, w in enumerate(words):
            if len(w) >= 5 and w not in vocab:
                close = get_close_matches(w, vocab, n=1, cutoff=0.82)
                if close:
                    words[i] = close[0]
        text = " ".join(words)
    except Exception:
        pass
    return text
