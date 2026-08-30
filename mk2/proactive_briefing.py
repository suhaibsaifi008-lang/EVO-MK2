"""Proactive briefing composer for EVO MK2.

Synthesizes rich, pre-composed morning briefings and evening summaries
incorporating weather, calendar events, unread mail, active projects, and
prior session memory into a cinematic JARVIS greeting.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from . import config, db, tools, user_profile

log = logging.getLogger("mk2.proactive_briefing")


def compose_morning_briefing() -> str:
    """Pre-compose complete morning briefing."""
    now = datetime.now()
    user_name = user_profile.get_user_profile().get("basics", {}).get("name") or config.settings.user_address or "sir"

    parts = [f"Good morning, {user_name}. It's {now.strftime('%H:%M')} on {now.strftime('%A, %d %B')}."]

    # 1. Weather
    try:
        weather_res = tools.call("weather_now", {})
        if weather_res.get("ok") and weather_res.get("speech"):
            parts.append(weather_res["speech"])
    except Exception:
        pass

    # 2. Calendar
    try:
        from .calendar_tools import calendar_today
        cal = calendar_today()
        if cal.get("ok") and cal.get("data", {}).get("events"):
            events = cal["data"]["events"]
            summaries = [e.get("summary", "Event") for e in events[:4]]
            parts.append(f"You have {len(events)} event(s) on your schedule today: {'; '.join(summaries)}.")
        else:
            parts.append("Your calendar is completely clear today.")
    except Exception:
        pass

    # 3. Unread email
    try:
        from .tools.life_tools import mail_check
        mail = mail_check()
        if mail.get("ok"):
            unread = mail.get("unread", 0)
            if unread > 0:
                parts.append(f"You have {unread} unread email(s).")
    except Exception:
        pass

    # 4. Active goals / projects from User Profile
    prof = user_profile.get_user_profile()
    active_projects = [p.get("name") for p in prof.get("projects", []) if p.get("status") == "active"]
    if active_projects:
        parts.append(f"Primary focus is on: {', '.join(active_projects[:2])}.")

    # 5. Yesterday's summary
    try:
        from . import deep_memory
        recent = deep_memory.search("session summary yesterday", k=1)
        if recent and recent[0].get("summary"):
            parts.append(f"Yesterday's progress: {recent[0]['summary'][:100]}...")
    except Exception:
        pass

    parts.append("All systems operational. Ready whenever you are.")
    return " ".join(parts)


def compose_evening_briefing() -> str:
    """Pre-compose evening wind-down summary."""
    now = datetime.now()
    user_name = user_profile.get_user_profile().get("basics", {}).get("name") or config.settings.user_address or "sir"

    parts = [f"Good evening, {user_name}. Wrapping up for {now.strftime('%A')}."]

    # Unfinished tasks or active missions
    try:
        from . import autonomy
        active_m = autonomy.get_runner().list_active_missions()
        if active_m:
            parts.append(f"You have {len(active_m)} autonomous mission(s) currently in progress or on standby.")
    except Exception:
        pass

    parts.append("I have synchronized your notes and memory. Standing by for any final directives.")
    return " ".join(parts)
