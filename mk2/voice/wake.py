"""Wake phrase matching (fuzzy-tolerant)."""
import re


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


WAKE_PHRASES = [p.strip().lower() for p in __import__("os").environ.get(
    "EVO_WAKE_PHRASES", "wake up evo,wake up e.v.o").split(",") if p.strip()]
FUZZY = 0.8


def match_wake(text: str) -> str | None:
    """Return remainder after a wake phrase, or None."""
    from difflib import SequenceMatcher

    t = normalize(text)
    if not t:
        return None
    words = t.split()
    joined = " ".join(words)
    compact = t.replace(" ", "")
    for phrase in WAKE_PHRASES:
        p = normalize(phrase)
        pw = p.split()
        idx = joined.find(p)
        if idx >= 0:
            return joined[idx + len(p):].strip()
        c = compact.find(p.replace(" ", ""))
        if c >= 0 and c <= 2:
            return ""
        n = len(pw)
        for i in range(max(1, len(words) - n + 1)):
            window = " ".join(words[i:i + n])
            if SequenceMatcher(None, window, p).ratio() >= FUZZY:
                return " ".join(words[i + n:]).strip()
    return None


def is_exit(text: str) -> bool:
    t = normalize(text)
    exits = ("stop listening", "go to sleep", "goodbye", "end session",
             "that will be all")
    return any(e in t for e in exits)
