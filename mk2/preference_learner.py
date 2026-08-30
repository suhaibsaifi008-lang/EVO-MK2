"""User Preference Learner for EVO MK2 (JARVIS Phase 13)."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Optional

from . import db

log = logging.getLogger("mk2.preference_learner")


class PreferenceLearner:
    """Infers and persists user working preferences from feedback."""

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS user_learned_preferences (
                        category TEXT PRIMARY KEY,
                        preference_json TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
        except Exception as exc:
            log.warning("Preference schema error: %s", exc)

    def record_feedback(self, category: str, preference_data: dict[str, Any]) -> None:
        now = time.time()
        current = self.get_preference(category)
        current.update(preference_data)
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute(
                    "INSERT OR REPLACE INTO user_learned_preferences (category, preference_json, updated_at) VALUES (?, ?, ?)",
                    (category, json.dumps(current), now),
                )
        except Exception as exc:
            log.warning("Failed recording user preference: %s", exc)

    def get_preference(self, category: str) -> dict[str, Any]:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                row = con.execute("SELECT preference_json FROM user_learned_preferences WHERE category = ?", (category,)).fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        except Exception:
            pass

        defaults = {
            "email_tone": {"tone": "direct_and_polite", "max_sentences": 4},
            "meeting_style": {"buffer_minutes": 15, "prefer_mornings": True},
            "proposal_style": {"lead_with_solution": True, "mention_portfolio": False},
            "user_profile": {
                "skills": "Python, Web Scraping, Automation, AI Agents, Playwright",
                "title": "Freelance Developer",
                "hourly_rate": 75,
                "bio": "I build autonomous systems and automation tools.",
            },
        }
        return defaults.get(category, {})

    def get_system_prompt_preferences(self) -> str:
        prefs = [
            f"- Email tone: {self.get_preference('email_tone').get('tone', 'direct')}",
            f"- Meeting buffer: {self.get_preference('meeting_style').get('buffer_minutes', 15)}m between events",
        ]
        return "\n".join(prefs)


_global_pref: Optional[PreferenceLearner] = None


def get_preference_learner() -> PreferenceLearner:
    global _global_pref
    if _global_pref is None:
        _global_pref = PreferenceLearner()
    return _global_pref
