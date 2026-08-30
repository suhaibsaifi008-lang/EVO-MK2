"""Central User Profile Store for EVO MK2 (JARVIS Fix C & Profile System).

Manages user identity, skills, goals, projects, and working hours with persistence,
depth scoring, and LLM prompt formatting.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .config import DATA

log = logging.getLogger("mk2.user_profile")
USER_PROFILE_PATH = DATA / "user_profile.json"

DEFAULT_PROFILE: dict[str, Any] = {
    "basics": {
        "name": "User",
        "title": "Freelance Developer & Automation Architect",
        "bio": "I build autonomous cognitive systems, web automation pipelines, and AI agent architectures.",
        "timezone": "Asia/Kolkata",
    },
    "skills": ["Python", "Web Scraping", "Automation", "AI Agents", "Playwright"],
    "goals": {
        "monthly_income": 5000,
        "weekly_hours": 35,
    },
    "projects": [],
    "working_hours": {
        "start": 9,
        "end": 23,
        "max_per_day": 8,
    },
    "depth_score": 25,
}


def _calc_depth(prof: dict[str, Any]) -> int:
    score = 10
    basics = prof.get("basics", {})
    if basics.get("name") and basics.get("name") != "User":
        score += 15
    if basics.get("title"):
        score += 10
    if basics.get("bio"):
        score += 10
    skills = prof.get("skills", [])
    if isinstance(skills, list):
        score += min(len(skills) * 5, 25)
    elif isinstance(skills, str) and skills:
        score += 15
    projects = prof.get("projects", [])
    if projects:
        score += min(len(projects) * 15, 30)
    return min(score, 100)


def get_user_profile() -> dict[str, Any]:
    """Return user profile dictionary."""
    if USER_PROFILE_PATH.exists():
        try:
            data = json.loads(USER_PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("depth_score", _calc_depth(data))
                return data
        except Exception as exc:
            log.warning("Failed loading user profile JSON: %s", exc)
    prof = dict(DEFAULT_PROFILE)
    prof["depth_score"] = _calc_depth(prof)
    return prof


def save_user_profile(prof: dict[str, Any]) -> None:
    """Save user profile dictionary to disk."""
    USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    prof["depth_score"] = _calc_depth(prof)
    try:
        USER_PROFILE_PATH.write_text(json.dumps(prof, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Failed saving user profile: %s", exc)


def format_profile_prompt() -> str:
    """Format user profile as context for LLM reasoning."""
    prof = get_user_profile()
    basics = prof.get("basics", {})
    skills = prof.get("skills", [])
    skills_str = ", ".join(skills) if isinstance(skills, list) else str(skills)
    projects = prof.get("projects", [])
    proj_lines = [f"  - {p.get('name')}: {p.get('status', 'active')}" for p in projects if isinstance(p, dict)]

    lines = [
        f"User Identity: {basics.get('name', 'User')} ({basics.get('title', '')})",
        f"Bio: {basics.get('bio', '')}",
        f"Core Skills: {skills_str}",
    ]
    if proj_lines:
        lines.append("Key Projects:")
        lines.extend(proj_lines)
    return "\n".join(lines)


def get_skills() -> str:
    prof = get_user_profile()
    skills = prof.get("skills", [])
    if isinstance(skills, list):
        return ", ".join(skills)
    return str(skills or "Python, Web Scraping, Automation, AI Agents")


class UserProfile:
    """Class wrapper for user profile management."""

    def __init__(self):
        pass

    def get(self, key: str, default: Any = None) -> Any:
        prof = get_user_profile()
        if key in prof:
            return prof[key]
        if key in prof.get("basics", {}):
            return prof["basics"][key]
        if key in prof.get("goals", {}):
            return prof["goals"][key]
        if key in prof.get("working_hours", {}):
            return prof["working_hours"][key]
        return default

    def set(self, key: str, value: Any) -> None:
        prof = get_user_profile()
        if key in prof.get("basics", {}):
            prof["basics"][key] = value
        elif key in prof.get("goals", {}):
            prof["goals"][key] = value
        elif key in prof.get("working_hours", {}):
            prof["working_hours"][key] = value
        else:
            prof[key] = value
        save_user_profile(prof)

    def get_skills(self) -> str:
        return get_skills()

    def get_context_for_llm(self) -> str:
        return format_profile_prompt()
