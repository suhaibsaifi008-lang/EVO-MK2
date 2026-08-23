"""Deterministic command fast-lane: instant execution, zero model calls.

Supports compound commands ("open youtube and search for cats") by splitting
on conjunctions and running each part through the same lanes.
"""
import re
import threading as _th_mod

from .bus import bus
from . import tools
from .tools.system_tools import APP_ALIASES, SITES

# Words that mark a part as actionable when splitting compounds.
_ACTION_RE = re.compile(
    r"^(?:open|close|launch|search|google|look up|research|play|volume|mute|unmute|"
    r"screenshot|remind|reminder|set|take|capture|what|who|when|where)\b")


def _normalize(text: str) -> str:
    t = (text or "").lower().strip(" .!?")
    t = re.sub(r"^(?:please|can you|could you|hey|evo)\s+", "", t)
    t = re.sub(r"\s+(?:for me|please|now)$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_compound(t: str) -> list[str]:
    """Split 'open youtube and play lofi' into two commands when BOTH sides
    are actionable. Single-intent sentences pass through untouched."""
    parts = re.split(r"\s+and\s+|\s+then\s+|\s*;\s*", t)
    if len(parts) < 2:
        return [t]
    actionable = [p for p in parts if _ACTION_RE.search(p)]
    if len(actionable) == len(parts):
        return [p.strip() for p in parts]
    return [t]


def _youtube_search_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


def fast_command(text: str) -> str | None:
    """Execute obvious commands directly. Returns spoken reply or None."""
    t = _normalize(text)
    if not t:
        return None

    parts = _split_compound(t)
    if len(parts) > 1:
        replies = []
        for part in parts:
            r = fast_command(part)
            if r:
                replies.append(r)
        if replies:
            return "; ".join(replies)
        return None

    return _single(t)


def _single(t: str) -> str | None:
    # volume / media -------------------------------------------------------
    if re.fullmatch(r"(?:volume|sound) (?:up|louder)", t):
        return tools.call("volume", {"action": "up"})["speech"]
    if re.fullmatch(r"(?:volume|sound) (?:down|quieter)", t):
        return tools.call("volume", {"action": "down"})["speech"]
    if re.fullmatch(r"(?:mute|unmute)(?: the(?: sound| volume))?", t):
        return tools.call("volume", {"action": "mute"})["speech"]

    # screen awareness -----------------------------------------------------
    if re.search(r"(what.s on my screen|on my screen right now|read my screen|see my screen)", t):
        r = tools.call("screen_read")
        return r["speech"] if r["ok"] else None

    # play X (on youtube) -> show search results --------------------------
    m = re.fullmatch(r"play (.+?)(?: on youtube)?", t)
    if m:
        import webbrowser

        q = m.group(1).strip()
        # vague asks after YouTube was opened = just go to YouTube
        if q.lower() in ("the first video", "first video", "a video",
                          "something", "anything", "videos"):
            webbrowser.open("https://www.youtube.com")
            return "YouTube is open."
        url = _youtube_search_url(q)
        webbrowser.open(url)
        return f"Showing YouTube results for '{q}' — click the first video."

    # research -> background job, FULL REPORT shown in chat when done ------
    m = re.fullmatch(r"(?:deep )?research(?: about)? (.+)", t)
    if m:
        topic = m.group(1).strip()
        if topic:

            def _bg(topic=topic):
                res = tools.call("deep_research", {"topic": topic})
                # Show the FULL report in chat so the user can read it
                report = res.get("data", {}).get("report", "")
                text = report if report else res.get("speech", "Research failed.")
                bus.publish("notify.out", {
                    "kind": "research",
                    "text": f"📄 Research complete:\n\n{text}",
                })

            _th_mod.Thread(target=_bg, daemon=True, name="mk2-research").start()
            return (f"Starting deep research on '{topic}'. Report will appear here "
                    "when it's ready.")

    # open X (general: aliases, sites, URLs, typos, Start Menu, fallback) --
    m = re.fullmatch(r"open(?: up)? (?:the |my |an? )?(.+?)(?: app| website| site)?", t)
    if m:
        target = m.group(1).strip()

        # Special case: "open youtube and ..." was already split above, so a
        # bare open here is single-intent.
        r = tools.call("open_app", {"target": target})
        return r["speech"]

    # close X ---------------------------------------------------------------
    m = re.fullmatch(r"close (?:the |my |all )?(.+)", t)
    if m:
        r = tools.call("close_app", {"target": m.group(1).strip()})
        return r["speech"]

    # reminders --------------------------------------------------------------
    # Extract ONLY the reminder part from mixed input like
    # "ok do that and also remind me to drink water in 1 minute"
    reminder_text = None
    m = re.search(r"(remind me to .+|set a reminder.+)", t)
    if m:
        reminder_text = m.group(1).strip()
    elif t.startswith(("remind me", "reminder", "set a reminder")):
        reminder_text = t

    if reminder_text:
        from .timeparse import parse_when, strip_when
        from . import db

        due = parse_when(reminder_text)
        body = strip_when(reminder_text)
        if due:
            db.reminder_add(body[:300], due.timestamp())
            hm = due.strftime('%H:%M')
            return f"Reminder set for {hm} ({body[:60]})."
        return None

    # screenshot --------------------------------------------------------------
    if re.fullmatch(r"(?:take a |capture a )?screenshot", t):
        r = tools.call("screenshot")
        return r["speech"] if r["ok"] else "Screenshot failed."

    # search ------------------------------------------------------------------
    m = re.fullmatch(r"(?:search(?: for)?|google|look up) (.+)", t)
    if m:
        r = tools.call("web_search", {"query": m.group(1).strip()})
        return r["speech"] if r["ok"] else f"Search failed: {r['speech']}"

    return None
