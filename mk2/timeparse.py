"""Deterministic natural-time parsing. Pure functions, fully tested."""
import re
from datetime import datetime, timedelta

WEEKDAYS = {d.lower(): i for i, d in enumerate(
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Understands: in 10 minutes/2 hours/30 seconds | at 9pm / 21:30 |
    tomorrow at 7am | monday 8:00 | today 18:30. Returns naive local dt."""
    now = now or datetime.now()
    t = (text or "").lower().strip()
    t = re.sub(r"^(?:remind me|set (?:an? )?(?:reminder|alarm)(?: for)?)\s*", "", t)
    t = t.strip(" .!?")

    m = re.search(r"\bin (\d+|\d*\.\d+) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", t)
    if m:
        n = float(m.group(1))
        unit = m.group(2)
        secs = n * {"s": 1, "m": 60, "h": 3600}[unit[0]]
        return now + timedelta(seconds=secs)

    hm = re.search(r"\bat (\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if not hm:
        hm = re.match(r"^(\d{1,2}):(\d{2})$", t)
    day_off = 0
    if re.search(r"\btomorrow\b", t):
        day_off = 1
    else:
        for name, idx in WEEKDAYS.items():
            if re.search(rf"\b{name}\b", t):
                delta = (idx - now.weekday()) % 7
                day_off = 7 if delta == 0 else delta
                break
    if hm:
        hour = int(hm.group(1))
        minute = int(hm.group(2) or 0)
        mer = hm.group(3) if hm.lastindex and hm.lastindex >= 3 and hm.group(3) else (
            (re.search(r"\b(am|pm)\b", t).group(1) if re.search(r"\b(am|pm)\b", t) else None))
        if mer == "pm" and hour < 12:
            hour += 12
        if mer == "am" and hour == 12:
            hour = 0
        due = (now + timedelta(days=day_off)).replace(hour=min(hour, 23),
                                                      minute=min(minute, 59), second=0, microsecond=0)
        if due <= now and day_off == 0:
            due += timedelta(days=1)
        return due

    # plain duration without 'in': "10 minutes"
    m = re.fullmatch(r"(\d+|\d*\.\d+) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?)", t)
    if m:
        n = float(m.group(1))
        secs = n * {"s": 1, "m": 60, "h": 3600}[m.group(2)[0]]
        return now + timedelta(seconds=secs)
    return None


def strip_when(text: str) -> str:
    """Remove time phrases, keep the message body."""
    t = text.strip()
    patterns = [
        r"\bin\s+\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
        r"\bat\s+\d{1,2}:\d{2}\s*(?:am|pm)?\b",
        r"\bat\s+\d{1,2}\s*(?:am|pm)\b",
        r"\b(?:today|tomorrow|tonight)\b",
    ]
    for pat in patterns:
        t = re.sub(pat, " ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"^\s*(?:remind me\s*(?:to\s+|about\s+|that\s+)?|please\s+)",
        "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip(" ,.")
