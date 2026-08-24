"""Strict command grammar built from the machine's real apps/sites."""
import json
import os
import re
import shutil
import subprocess
import threading
import time

_cache = {"phrases": None, "ts": 0.0}
_lock = threading.Lock()

CORE = {
    "open calculator", "open notepad", "open chrome", "open edge",
    "open firefox", "open brave", "open spotify", "open discord",
    "open steam", "open terminal", "open explorer", "open files",
    "open settings", "volume up", "volume down", "mute", "unmute",
    "what time is it", "what is the time", "take a screenshot",
    "screenshot", "goodbye", "stop listening", "go to sleep",
}
TARGETS = {
    "youtube", "google", "gmail", "maps", "github", "reddit",
    "wikipedia", "chatgpt", "gemini", "netflix", "amazon", "flipkart",
    "linkedin", "twitch", "minecraft", "fortnite", "roblox", "valorant",
    "calculator", "notepad", "paint", "explorer", "terminal", "settings",
    "spotify", "discord", "steam", "whatsapp", "telegram", "vlc",
    "brave", "chrome", "edge", "firefox", "camera", "word", "excel",
}


def _discover_more() -> set:
    extra = set()
    try:
        from pathlib import Path

        base = Path(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs")
        base2 = Path(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")
        for b in (base, base2):
            if b.exists():
                for p in list(b.rglob("*.lnk"))[:400]:
                    stem = p.stem.lower().strip()
                    if re.fullmatch(r"[a-z][a-z0-9 ]{2,20}", stem):
                        extra.add(stem)
    except Exception:
        pass
    return extra


def grammar_phrases(max_phrases: int = 900) -> list[str]:
    with _lock:
        now = time.time()
        if _cache["phrases"] and now - _cache["ts"] < 600:
            return _cache["phrases"]
    targets = TARGETS | {t for t in _discover_more() if t not in TARGETS}
    phrases = sorted(CORE)
    for t in sorted(targets):
        phrases.append(f"open {t}")
        phrases.append(f"close {t}")
        phrases.append(f"search for {t}")
    phrases.extend(sorted(targets))
    out = phrases[:max_phrases]
    with _lock:
        _cache["phrases"] = out
        _cache["ts"] = now
    return out


def grammar_json() -> str:
    return json.dumps(grammar_phrases(), ensure_ascii=False)


VERBS = ("open ", "close ", "search ", "play ", "start ")


def trust_grammar(g: str, general: str) -> bool:
    from difflib import SequenceMatcher

    g = re.sub(r"\s+", " ", (g or "").lower()).strip()
    gen = re.sub(r"\s+", " ", (general or "").lower()).strip()
    if not g or not gen:
        return False
    polite = ("please ", "can you ", "could you ", "hey ", "evo ")
    for p in polite:
        if gen.startswith(p):
            gen = gen[len(p):]
    long_chat = len(gen.split()) >= 6 and not gen.startswith(VERBS)
    verb_cmd = (
        any(g.startswith(v) for v in VERBS) and len(g.split()) <= 5
        and not long_chat
    )
    if verb_cmd:
        return True
    if long_chat:
        return False
    return SequenceMatcher(None, gen, g).ratio() >= 0.6
