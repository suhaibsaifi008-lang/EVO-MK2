"""Deterministic command fast-lane: instant execution, zero model calls.

Anything matched here never reaches an LLM — this is the latency fix.
"""
import re

from . import tools
from .tools.system_tools import APP_ALIASES, SITES


def _normalize(text: str) -> str:
    t = text.lower().strip(" .!?")
    t = re.sub(r"^(?:please|can you|could you|hey|evo)\s+", "", t)
    t = re.sub(r"\s+(?:for me|please|now)$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def fast_command(text: str) -> str | None:
    """Execute obvious commands directly. Returns spoken reply or None."""
    t = _normalize(text)
    if not t:
        return None

    # volume / media
    if re.fullmatch(r"(volume|sound) (up|louder)", t):
        r = tools.call("volume", {"action": "up"});  return r["speech"]
    if re.fullmatch(r"(volume|sound) (down|quieter)", t):
        r = tools.call("volume", {"action": "down"}); return r["speech"]
    if re.fullmatch(r"(mute|unmute)( the( sound| volume))?", t):
        r = tools.call("volume", {"action": "mute"}); return r["speech"]

    # open X   (app alias, known site, or URL-ish)
    m = re.fullmatch(r"open(?: up)? (?:the |my )?(.+?)(?: app| website| site)?", t)
    if m:
        target = m.group(1).strip()
        key = target.replace(" browser", "").strip()
        if key in APP_ALIASES or target in APP_ALIASES or key.endswith(":"):
            r = tools.call("open_app", {"target": key if key in APP_ALIASES else target})
            return r["speech"]
        if target in SITES or key in SITES:
            r = tools.call("open_app", {"target": target})
            return r["speech"]
        if "." in target and " " not in target and len(target.split(".")[-1]) <= 4:
            r = tools.call("open_app", {"target": target})
            return r["speech"]

    # reminders
    if t.startswith(("remind me", "reminder", "set a reminder")) or " remind me to " in t:
        from .timeparse import parse_when, strip_when
        from . import db
        due = parse_when(t)
        body = strip_when(t)
        if due:
            rid = db.reminder_add(body[:300], due.timestamp())
            hm = due.strftime('%H:%M')
            return f"Reminder set for {hm} ({body[:60]})."
        return None

    # screenshot
    if re.fullmatch(r"(take a |capture a )?screenshot", t):
        r = tools.call("screenshot")
        return r["speech"] if r["ok"] else "Screenshot failed."

    # search X  → run real web search, speak top titles instantly
    m = re.fullmatch(r"(?:search(?: for)?|google|look up) (.+)", t)
    if m:
        q = m.group(1).strip()
        r = tools.call("web_search", {"query": q})
        return r["speech"] if r["ok"] else f"Search failed: {r['speech']}"

    return None
