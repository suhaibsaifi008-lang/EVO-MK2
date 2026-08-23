"""Calendar tools: read-only iCal feed (today / upcoming)."""
import re
import urllib.request
from datetime import datetime, timedelta

from . import db
from .tools import tool


def _fetch_ics() -> str:
    url = db.get_setting("calendar_ical_url", "").strip()
    if not url:
        raise RuntimeError("No calendar URL set (Settings → calendar_ical_url).")
    req = urllib.request.Request(url, headers={"User-Agent": "EVO-MK2"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def _unfold(raw: str) -> list:
    lines = []
    for line in raw.splitlines():
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line.rstrip("\r"))
    return lines


def _parse_dt(value: str):
    m = re.match(r"^(\d{8})T(\d{6})(Z)?$", value.strip())
    if not m:
        return None
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc).astimezone()
    return dt.replace(tzinfo=None)


def fetch_events(days: int = 7) -> list[dict]:
    raw = _fetch_ics()
    now = datetime.now()
    horizon = now + timedelta(days=max(1, int(days)))
    events = []
    cur = None
    for line in _unfold(raw):
        if line.startswith("BEGIN:VEVENT"):
            cur = {}
        elif line.startswith("END:VEVENT"):
            if cur and cur.get("start") and cur["start"] <= horizon:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            base = key.split(";")[0].upper()
            val = val.strip()
            if base == "DTSTART":
                cur["start"] = _parse_dt(val)
            elif base == "DTEND":
                cur["end"] = _parse_dt(val)
            elif base == "SUMMARY":
                cur["title"] = val[:120]
    events.sort(key=lambda e: e.get("start") or now)
    return [e for e in events if (e.get("start") or now + timedelta(days=99)) >= now - timedelta(days=1)]


@tool("calendar_today", "Today's calendar events.", {}, permission="read")
def calendar_today() -> dict:
    today = datetime.now().date()
    evs = [e for e in fetch_events(days=3)
           if isinstance(e.get("start"), datetime) and e["start"].date() == today]
    if not evs:
        return {"ok": True, "speech": "Nothing on the calendar today.", "data": {"events": []}}
    lines = [f"{e['start'].strftime('%H:%M')} {e.get('title', '(untitled)')}" for e in evs]
    return {"ok": True, "speech": "; ".join(lines), "data": {"events": evs}}


@tool("calendar_upcoming", "Upcoming calendar events.", {"days": {"type": "integer"}}, permission="read")
def calendar_upcoming(days: int = 7) -> dict:
    evs = fetch_events(days=max(1, min(int(days), 30)))
    if not evs:
        return {"ok": True, "speech": "Calendar is clear.", "data": {"events": []}}
    lines = [f"{e['start'].strftime('%a %d %H:%M')} {e.get('title', '')}" for e in evs[:6]]
    return {"ok": True, "speech": "; ".join(lines), "data": {"events": evs}}
