"""Hybrid TTS: instant local SAPI for short lines, neural edge-tts for long."""
import asyncio
import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from ..config import DATA

log = logging.getLogger("mk2.voice.tts")

TTS_DIR = DATA / "tts"
TTS_DIR.mkdir(parents=True, exist_ok=True)

CURATED_VOICES = [
    "en-US-AndrewMultilingualNeural",   # most humanlike, warm male
    "en-US-AvaMultilingualNeural",      # most humanlike, expressive female
    "en-US-BrianMultilingualNeural",    # natural, casual male
    "en-IN-PrabhatNeural",              # Indian English, male
    "en-IN-NeerjaNeural",               # Indian English, female
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-US-GuyNeural",
    "en-US-AriaNeural",
]

_MD_STRIP = [
    (re.compile(r"\*\*\*(.+?)\*\*\*", re.S), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"\1"),
    (re.compile(r"\*(.+?)\*", re.S), r"\1"),
    (re.compile(r"__(.+?)__", re.S), r"\1"),
    (re.compile(r"(?<![a-zA-Z0-9])_(.+?)_(?![a-zA-Z0-9])", re.S), r"\1"),
    (re.compile(r"`+"), ""),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
]


def sanitize_speech(text: str) -> str:
    """Strip markdown emphasis/markup so TTS never says 'asterisk'."""
    t = str(text or "")
    for pat, rep in _MD_STRIP:
        t = pat.sub(rep, t)
    return re.sub(r"[ \t]{2,}", " ", t).replace("\n\n\n", "\n\n").strip()


def select_voice(formality: int | None = None, preference: str = "") -> str:
    import os
    env_voice = os.environ.get("EVO_TTS_VOICE", "").strip()
    if env_voice:
        return env_voice
    if preference:
        for v in CURATED_VOICES:
            if preference.lower() in v.lower():
                return v
    if formality is None:
        try:
            from ..persona_loader import get_persona_state
            ps = get_persona_state()
            formality = getattr(ps, "formality", 60)
        except Exception:
            formality = 60
    if formality > 75:
        return "en-GB-RyanNeural"
    elif formality < 40:
        return "en-US-BrianMultilingualNeural"
    return "en-US-AndrewMultilingualNeural"


def _edge_voice() -> str:
    return select_voice()


def _edge_rate() -> str:
    """Speech rate for edge-tts, e.g. '+25%'. Env: EVO_TTS_RATE."""
    import os

    return os.environ.get("EVO_TTS_RATE", "+25%").strip()


def _sapi_rate() -> int:
    """SAPI rate 0-10 (~default 3 == +25% vs normal). Env: EVO_SAPI_RATE."""
    import os

    try:
        return max(-5, min(10, int(os.environ.get("EVO_SAPI_RATE", "3"))))
    except ValueError:
        return 3


def _sapi_voice_clause() -> str:
    """Optional EVO_SAPI_VOICE: substring of a Windows voice name, e.g.
    'Zira' or 'Hemant'. Empty = system default."""
    import os

    want = os.environ.get("EVO_SAPI_VOICE", "").strip()
    if not want:
        return ""
    import re
    safe = re.sub(r"[^\w\s\-]", "", want).strip()
    if not safe:
        return ""
    try:
        match = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-SpeechVoice | Where-Object Name -like '*{safe}*'"
             " | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if match:
            safe_match = re.sub(r"[^\w\s\-]", "", match).strip().replace("'", "''")
            if safe_match:
                return f"$s.SelectVoice('{safe_match}');"
    except Exception:
        pass
    return ""


def _sapi_wav(text: str) -> Path | None:
    text = sanitize_speech(text)
    key = hashlib.sha1(("sapi|" + os.environ.get("EVO_SAPI_VOICE", "") +
                        "|" + text).encode()).hexdigest()[:20] + ".wav"
    out = TTS_DIR / key
    if out.exists() and out.stat().st_size > 512:
        return out
    tmp = out.with_suffix(".part")
    import base64
    ps = (
        "Add-Type -AssemblyName System.Speech;\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;\n"
        f"$s.Rate = {_sapi_rate()};\n"
        + _sapi_voice_clause() + "\n"
        + f"$s.SetOutputToWaveFile(@'\n{tmp}\n'@);\n"
        + f"$s.Speak(@'\n{text}\n'@);\n"
        + "$s.Dispose()"
    )
    encoded_cmd = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_cmd],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60,
        )
        tmp.replace(out)
        return out
    except Exception:
        tmp.unlink(missing_ok=True)
        return None


def _elevenlabs_mp3(text: str, stop: threading.Event) -> Path | None:
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return None
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip()
    key_src = "el:" + voice_id + ":" + text
    key = hashlib.sha1(key_src.encode()).hexdigest()[:20] + ".mp3"
    out = TTS_DIR / key
    if out.exists() and out.stat().st_size > 512:
        return out

    try:
        from ..persona_loader import get_persona_state
        ps = get_persona_state()
        mood = getattr(ps, "mood", "focused")
    except Exception:
        mood = "focused"

    stability = 0.55
    similarity = 0.75
    if mood == "concerned":
        stability = 0.70
    elif mood == "enthusiastic":
        stability = 0.35
        similarity = 0.85

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text[:1000],
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
        },
    }
    try:
        import json
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()
            if stop.is_set():
                return None
            tmp = out.with_suffix(".part")
            tmp.write_bytes(data)
            if tmp.exists() and tmp.stat().st_size > 256:
                tmp.replace(out)
                return out
    except Exception as exc:
        log.debug("ElevenLabs synthesis skipped/failed: %s", exc)
        tmp = out.with_suffix(".part")
        tmp.unlink(missing_ok=True)
    return None


def _edge_mp3(text: str, stop: threading.Event) -> Path | None:
    text = sanitize_speech(text)
    try:
        import edge_tts

        key = hashlib.sha1(("edge|" + _edge_voice() + "|" +
                            _edge_rate() + "|" + text).encode()
                           ).hexdigest()[:20] + ".mp3"
        out = TTS_DIR / key
        if out.exists() and out.stat().st_size > 512:
            return out
        tmp = out.with_suffix(".part")

        async def gen():
            com = edge_tts.Communicate(text[:800], _edge_voice(),
                                       rate=_edge_rate())
            await asyncio.wait_for(com.save(str(tmp)), timeout=25)

        try:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(gen())
            finally:
                loop.close()
            if tmp.exists() and tmp.stat().st_size > 256:
                tmp.replace(out)
                return out
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    return None


# ---------------- piper (local neural, primary engine) ----------------
# Voice: jgkawell/jarvis from HuggingFace (free, CPU, ~60 MB medium).
# Files cached under DATA/models/piper via huggingface_hub local_dir.

PIPER_DIR = DATA / "models" / "piper"
_PIPER_LOCK = threading.Lock()
_piper_voice = None          # loaded singleton
_piper_failed = False        # set on broken install, resets after cooldown
_piper_failed_ts = 0.0       # timestamp of last failure
_PIPER_COOLDOWN = 300        # retry after 5 minutes


def _piper_repo() -> str:
    return os.environ.get("EVO_PIPER_REPO", "jgkawell/jarvis").strip()


def _piper_voice_file() -> str:
    """Repo-relative path of the ONNX voice. Env: EVO_PIPER_VOICE."""
    return os.environ.get(
        "EVO_PIPER_VOICE", "en/en_GB/jarvis/medium/jarvis-medium.onnx").strip()


def _piper_paths() -> tuple[Path, Path]:
    # hf_hub_download(local_dir=...) preserves the repo subpath:
    # PIPER_DIR / en/en_GB/jarvis/medium/jarvis-medium.onnx
    model = PIPER_DIR / Path(_piper_voice_file())
    config = model.with_name(model.name + ".json")
    if not (model.exists() and model.stat().st_size > 1024
            and config.exists()):
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        from huggingface_hub import hf_hub_download

        for fn in (_piper_voice_file(), _piper_voice_file() + ".json"):
            hf_hub_download(repo_id=_piper_repo(), filename=fn,
                            local_dir=str(PIPER_DIR))
    return model, config


def _piper_load():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    with _PIPER_LOCK:
        if _piper_voice is None:
            from piper import PiperVoice

            model, config = _piper_paths()
            _piper_voice = PiperVoice.load(model, config_path=config)
    return _piper_voice


def piper_available() -> bool:
    global _piper_failed, _piper_failed_ts
    with _PIPER_LOCK:
        if _piper_failed:
            if (time.time() - _piper_failed_ts) < _PIPER_COOLDOWN:
                return False
            _piper_failed = False  # cooldown expired, retry
    try:
        _piper_load()
        return True
    except Exception as exc:  # noqa: BLE001
        with _PIPER_LOCK:
            _piper_failed = True
            _piper_failed_ts = time.time()
        print(f"[tts] piper unavailable: {str(exc)[:160]}", flush=True)
        return False


def warm_piper() -> None:
    """Preload the voice in a daemon thread so first reply pays no load tax."""

    def run() -> None:
        try:
            v = _piper_load()
            v.synthesize("Voice systems online.")
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="mk2-piper-warm").start()


def _piper_wav(text: str) -> Path | None:
    text = sanitize_speech(text)
    if not text:
        return None
    key = hashlib.sha1(f"piper|{_piper_repo()}|{_piper_voice_file()}|{text}".encode()).hexdigest()[:20] + ".wav"
    out = TTS_DIR / key
    if out.exists() and out.stat().st_size > 512:
        return out
    import wave

    try:
        voice = _piper_load()
        chunks = list(voice.synthesize(text[:3500]))
        if not chunks:
            return None
        sr = chunks[0].sample_rate
        sw = chunks[0].sample_width
        ch = chunks[0].sample_channels
        payload = b"".join(c.audio_int16_bytes for c in chunks)
        tmp = out.with_suffix(".part")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(sw)
            w.setframerate(sr)
            w.writeframes(payload)
        tmp.replace(out)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] piper synth failed: {str(exc)[:120]}", flush=True)
        tmp = out.with_suffix(".part")
        tmp.unlink(missing_ok=True)
        return None


def _mci_play(path: Path, stop: threading.Event, alias: str) -> bool:
    import ctypes

    winmm = ctypes.windll.winmm
    if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0) != 0:
        return False
    try:
        winmm.mciSendStringW(f"play {alias}", None, 0, 0)
        buf = ctypes.create_unicode_buffer(64)
        while True:
            if stop.is_set():
                break
            winmm.mciSendStringW(f"status {alias} mode", buf, 64, 0)
            if buf.value.strip().lower() != "playing":
                break
            time.sleep(0.12)
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)
    return True

class Speaker:
    def __init__(self) -> None:
        self.stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._n = 0
        self._active = False          # True from say() until playback ends
        self._current_alias: str | None = None
        self._lock = threading.Lock()

    def say(self, text: str) -> threading.Event:
        """Speak text; returns the stop Event (set it to cut speech)."""
        with self._lock:
            # Cut off any currently playing speech immediately
            self.stop_evt.set()
            if self._current_alias:
                try:
                    import ctypes
                    ctypes.windll.winmm.mciSendStringW(f"stop {self._current_alias}", None, 0, 0)
                    ctypes.windll.winmm.mciSendStringW(f"close {self._current_alias}", None, 0, 0)
                except Exception:
                    pass
                self._current_alias = None
            self.stop_evt = threading.Event()
            stop = self.stop_evt
            self._active = True
            self._thread = threading.Thread(
                target=self._speak, args=(text, stop), daemon=True,
                name="mk2-tts"
            )
            self._thread.start()
            return stop

    def shut_up(self) -> None:
        """Immediately cut off all active audio output and signals."""
        with self._lock:
            self.stop_evt.set()
            self._active = False
            if self._current_alias:
                try:
                    import ctypes
                    ctypes.windll.winmm.mciSendStringW(f"stop {self._current_alias}", None, 0, 0)
                    ctypes.windll.winmm.mciSendStringW(f"close {self._current_alias}", None, 0, 0)
                except Exception:
                    pass
                self._current_alias = None

    @property
    def is_speaking(self) -> bool:
        if self._active and self.stop_evt.is_set():
            return False              # interrupted
        return self._active

    def _speak(self, text: str, stop: threading.Event) -> None:
        alias = None
        try:
            text = sanitize_speech(" ".join((text or "").split())[:3500])
            if not text or stop.is_set():
                return
            with self._lock:
                self._n += 1
                cur_n = self._n
            alias = f"mk2_{cur_n}_{int(time.time()*1000)%1000000}"
            engine = os.environ.get("EVO_TTS_ENGINE", "auto")
            path = None
            if engine in ("auto", "elevenlabs") and not stop.is_set():
                path = _elevenlabs_mp3(text, stop)
            if path is None and engine in ("auto", "piper") and not stop.is_set():
                path = _piper_wav(text)
            if path is None and engine in ("auto", "edge") and not stop.is_set():
                path = _edge_mp3(text, stop)
            if path is None and engine in ("auto", "sapi"):
                path = _sapi_wav(text)
            if path is None or stop.is_set():
                return
            ext = path.suffix.lower()
            ttype = "waveaudio" if ext == ".wav" else "mpegvideo"
            import ctypes

            winmm = ctypes.windll.winmm
            if winmm.mciSendStringW(f'open "{path}" type {ttype} alias {alias}', None, 0, 0) != 0:
                return
            with self._lock:
                if stop.is_set():
                    winmm.mciSendStringW(f"close {alias}", None, 0, 0)
                    return
                self._current_alias = alias
            winmm.mciSendStringW(f"play {alias}", None, 0, 0)
            buf = ctypes.create_unicode_buffer(64)
            while not stop.is_set():
                winmm.mciSendStringW(f"status {alias} mode", buf, 64, 0)
                if buf.value.strip().lower() != "playing":
                    break
                time.sleep(0.08)
        except Exception as exc:
            log.warning("Speaker._speak error: %s", exc)
        finally:
            with self._lock:
                if self._current_alias == alias:
                    self._current_alias = None
                self._active = False
            if alias:
                try:
                    import ctypes
                    ctypes.windll.winmm.mciSendStringW(f"close {alias}", None, 0, 0)
                except Exception:
                    pass

    @staticmethod
    def cleanup(max_files: int = 200) -> None:
        files = sorted(TTS_DIR.glob("*.*"), key=lambda p: p.stat().st_mtime)
        for stale in files[:-max_files] if len(files) > max_files else []:
            try:
                stale.unlink()
            except OSError:
                pass


def list_sapi_voices() -> list[str]:
    import subprocess

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Add-Type -AssemblyName System.Speech;"
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
             ".GetInstalledVoices() | ForEach-Object "
             "{ $_.VoiceInfo.Name }"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except Exception:
        return []
