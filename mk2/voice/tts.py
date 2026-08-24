"""Hybrid TTS: instant local SAPI for short lines, neural edge-tts for long."""
import asyncio
import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

from ..config import DATA

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


def _edge_voice() -> str:
    import os

    return (os.environ.get("EVO_TTS_VOICE", "").strip()
            or "en-US-AndrewMultilingualNeural")


def _sapi_voice_clause() -> str:
    """Optional EVO_SAPI_VOICE: substring of a Windows voice name, e.g.
    'Zira' or 'Hemant'. Empty = system default."""
    import os

    want = os.environ.get("EVO_SAPI_VOICE", "").strip()
    if not want:
        return ""
    safe = want.replace("'", "''")
    try:
        match = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-SpeechVoice | Where-Object Name -like '*{safe}*'"
             " | Select-Object -First 1).Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
        if match:
            return f"$s.SelectVoice('{match}');"
    except Exception:
        pass
    return ""


def _sapi_wav(text: str) -> Path | None:
    key = hashlib.sha1(("sapi|" + os.environ.get("EVO_SAPI_VOICE", "") +
                        "|" + text).encode()).hexdigest()[:20] + ".wav"
    out = TTS_DIR / key
    if out.exists() and out.stat().st_size > 512:
        return out
    tmp = out.with_suffix(".part")
    safe = text.replace("'", "''")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        + _sapi_voice_clause() +
        f"$s.SetOutputToWaveFile('{tmp}');"
        f"$s.Speak('{safe}');"
        "$s.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=60,
        )
        tmp.replace(out)
        return out
    except Exception:
        return None


def _edge_mp3(text: str, stop: threading.Event) -> Path | None:
    try:
        import edge_tts

        key = hashlib.sha1(("edge|" + text).encode()).hexdigest()[:20] + ".mp3"
        out = TTS_DIR / key
        if out.exists() and out.stat().st_size > 512:
            return out
        tmp = out.with_suffix(".part")

        async def gen():
            com = edge_tts.Communicate(text[:800], _edge_voice())
            await asyncio.wait_for(com.save(str(tmp)), timeout=25)

        asyncio.run(gen())
        if tmp.exists() and tmp.stat().st_size > 256:
            tmp.replace(out)
            return out
    except Exception:
        pass
    return None


def _mci_play(path: Path, stop: threading.Event, alias: str) -> bool:
    import ctypes

    winmm = ctypes.windll.winmm
    if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0) != 0:
        return False
    winmm.mciSendStringW(f"play {alias}", None, 0, 0)
    buf = ctypes.create_unicode_buffer(64)
    while True:
        if stop.is_set():
            break
        winmm.mciSendStringW(f"status {alias} mode", buf, 64, 0)
        if buf.value.strip().lower() != "playing":
            break
        time.sleep(0.12)
    winmm.mciSendStringW(f"close {alias}", None, 0, 0)
    return True


class Speaker:
    def __init__(self) -> None:
        self.stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._n = 0

    def say(self, text: str) -> threading.Event:
        """Speak text; returns the stop Event (set it to cut speech)."""
        self.stop_evt.clear()
        self._thread = threading.Thread(
            target=self._speak, args=(text, self.stop_evt), daemon=True, name="mk2-tts"
        )
        self._thread.start()
        return self.stop_evt

    def shut_up(self) -> None:
        self.stop_evt.set()

    def _speak(self, text: str, stop: threading.Event) -> None:
        text = " ".join((text or "").split())[:600]
        if not text or stop.is_set():
            return
        self._n += 1
        alias = f"mk2{self._n}{int(time.time()*1000)%100000}"
        engine = os.environ.get("EVO_TTS_ENGINE", "auto")
        path = None
        if engine in ("auto", "sapi") and len(text) <= 90:
            path = _sapi_wav(text)
        if path is None and not stop.is_set():
            path = _edge_mp3(text, stop)
        if path is None:
            return
        ext = path.suffix.lower()
        ttype = "waveaudio" if ext == ".wav" else "mpegvideo"
        import ctypes

        winmm = ctypes.windll.winmm
        if winmm.mciSendStringW(f'open "{path}" type {ttype} alias {alias}', None, 0, 0) != 0:
            return
        winmm.mciSendStringW(f"play {alias}", None, 0, 0)
        buf = ctypes.create_unicode_buffer(64)
        while not stop.is_set():
            winmm.mciSendStringW(f"status {alias} mode", buf, 64, 0)
            if buf.value.strip().lower() != "playing":
                break
            time.sleep(0.12)
        winmm.mciSendStringW(f"close {alias}", None, 0, 0)

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
