"""Phase 7: Initiative engine — EVO speaks first, within strict limits.

A curiosity queue is composed from signals the earlier phases already
produce (pending reminders, fresh research, habit suggestions, security
findings). A kernel tick offers at most EVO_INITIATIVE_MAX messages per
day, never during quiet hours, never right after a live conversation,
and always through notify.out so every surface gets it.
"""
import json
import os
import threading
from datetime import datetime

from . import db

_lock = threading.Lock()
_state = {"day": "", "count": 0, "last_ts": 0.0}

MIN_GAP_S = 45 * 60
RECENT_CONVERSATION_S = 10 * 60


def _limits() -> tuple[int, int, int]:
    quiet_start = int(os.environ.get("EVO_QUIET_START", "23"))
    quiet_end = int(os.environ.get("EVO_QUIET_END", "8"))
    max_day = max(0, int(os.environ.get("EVO_INITIATIVE_MAX", "3")))
    return quiet_start, quiet_end, max_day


def _quiet_hours(now: datetime) -> bool:
    qs, qe, _ = _limits()
    return now.hour >= qs or now.hour < qe


def _user_recently_active() -> bool:
    rows = db.recent_messages(1)
    if not rows:
        return False
    import time as _t

    return (_t.time() - float(rows[0].get("ts") or 0)) < RECENT_CONVERSATION_S


def gather_candidates() -> list[str]:
    """Concrete, non-generic things worth mentioning. All optional."""
    out = []
    try:
        pending = db.reminders_pending()
        tomorrow = [r for r in pending]
        if len(tomorrow) >= 3:
            first = tomorrow[0]["text"][:60]
            out.append(f"You have {len(pending)} reminders pending - next up: "
                       f"'{first}'.")
    except Exception:
        pass
    try:
        from .vault import list_notes

        research = [n for n in list_notes() if n["topic"].startswith("research")]
        if research:
            topic = research[0]["topic"].replace("research ", "").replace(
                "-", " ")
            out.append(f"Earlier we researched '{topic}' - want me to pull "
                       "anything newer on it?")
    except Exception:
        pass
    try:
        proposals = db.proposals(status="pending")
        if proposals:
            out.append(f"There {'is' if len(proposals) == 1 else 'are'} "
                       f"{len(proposals)} automation suggestion(s) waiting "
                       "for your yes.")
    except Exception:
        pass
    return out


def compose(candidate: str) -> str:
    """Turn a raw candidate into one natural, human sentence."""
    from . import llm

    try:
        return llm.chat(
            [{"role": "system",
              "content": ("Turn this into ONE short, natural message from "
                          "EVO to its user. Casual, warm, no emoji spam, "
                          "no 'sir', max 2 sentences.")},
             {"role": "user", "content": candidate[:400]}],
            role="fast", temperature=0.6, timeout=12)
    except Exception:
        return candidate


def maybe_initiate(publish) -> bool:
    """Called by the kernel tick. Returns True when it spoke."""
    now = datetime.now()
    qs, qe, max_day = _limits()
    with _lock:
        today = now.strftime("%Y%m%d")
        if _state["day"] != today:
            _state["day"], _state["count"] = today, 0
        if _state["count"] >= max_day:
            return False
        import time as _t

        if _t.time() - _state["last_ts"] < MIN_GAP_S:
            return False
    if _quiet_hours(now) or _user_recently_active():
        return False
    candidates = gather_candidates()
    if not candidates:
        return False
    message = compose(candidates[0])
    publish("notify.out", {"kind": "initiative", "text": message})
    with _lock:
        _state["count"] += 1
        _state["last_ts"] = __import__("time").time()
    db.audit("initiative", candidates[0][:100], True, message[:120])
    return True


def status() -> dict:
    with _lock:
        return {"today_count": _state["count"],
                "max": _limits()[2],
                "quiet_hours": f"{_limits()[0]}:00-{_limits()[1]}:00"}


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("initiative_now", "Make EVO say something genuinely useful right now (one unprompted thought).",
      {}, permission="read")
def initiative_now() -> dict:
    candidates = gather_candidates()
    if not candidates:
        return {"ok": False,
                "speech": "Nothing interesting enough to bring up right now.",
                "data": {}}
    msg = compose(candidates[0])
    return {"ok": True, "speech": msg, "data": {"candidate": candidates[0]}}
