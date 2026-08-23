"""Deterministic command fast-lane: instant execution, zero model calls.

Anything matched here never reaches an LLM — this is the latency fix.
"""
import re
import threading as _th_mod

from .bus import bus

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

    # open X — fully general: delegates to open_app which handles apps,
    # sites, URLs, typos (fuzzy), Start-Menu discovery and web-search
    # fallback. Always instant.
    m = re.fullmatch(r"open(?: up)? (?:the |my )?(.+?)(?: app| website| site)?", t)
    if m:
        r = tools.call("open_app", {"target": m.group(1).strip()})
        return r["speech"]

    # close X
    m = re.fullmatch(r"close (?:the |my )?(.+)", t)
    if m:
        r = tools.call("close_app", {"target": m.group(1).strip()})
        return r["speech"]

    # screen awareness -> vision tool directly (no text-model detour)
    if re.search(r"(what.s on my screen|on my screen right now|read my screen|see my screen)", t):
        r = tools.call("screen_read")
        return r["speech"] if r["ok"] else None

    # deep research -> instant background job (no LLM decision needed)
    m = re.fullmatch(r"(?:deep )?research (.+)", t)
    if m:
        import threading as _th

        from .bus import bus

        topic = m.group(1).strip().rstrip(".")
        if not topic:
            return None

        def _bg():
            res = tools.call("deep_research", {"topic": topic})
            bus.publish("notify.out", {
                "kind": "research",
                "text": f"Research finished: {res.get('speech', '')[:200]}",
            })

        _th_mod.Thread(target=_bg, daemon=True, name="mk2-research").start()
        return (f"Starting deep research on '{topic}'. I'll save a cited report "
                "to your vault and tell you when it's done.")

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
