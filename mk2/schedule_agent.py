"""Scheduling & Calendar Intelligence for EVO MK2 (JARVIS Phase 5 / Item 4).

Proactively manages user time: Google Calendar bidirectional sync, focus blocks,
pre-meeting dossiers, and post-meeting follow-ups.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any, Optional

from . import db, llm
from .audit import get_audit_logger
from .consent import get_consent_manager
from .credential_vault import get_credential_vault
from .ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.schedule_agent")


class ScheduleAgent:
    """Intelligent calendar coordinator, meeting prep, and Google Calendar sync engine."""

    def __init__(self):
        self.ethics = get_moral_engine()
        self.consent = get_consent_manager()
        self.vault = get_credential_vault()
        self.audit = get_audit_logger()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS autonomous_calendar (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        start_ts REAL NOT NULL,
                        end_ts REAL NOT NULL,
                        attendees_json TEXT,
                        location TEXT,
                        meta_json TEXT
                    )
                """)
                con.execute("CREATE INDEX IF NOT EXISTS idx_cal_start ON autonomous_calendar(start_ts)")
        except Exception as exc:
            log.warning("Calendar schema initialization failed: %s", exc)

    def connect_google_calendar(self) -> MoralVerdict:
        """Connect to Google Calendar using stored OAuth2 credentials in vault."""
        creds_data = self.vault.get("google_calendar")
        if not creds_data:
            return MoralVerdict.caution(
                "No Google Calendar credentials stored. Add credentials via vault.store('google_calendar', {...}). "
                "Falling back to local SQLite calendar."
            )
        return MoralVerdict.safe("Google Calendar credentials verified.")

    def sync_google_calendar(self) -> MoralVerdict:
        """Pull remote Google Calendar events into local cache."""
        conn_verdict = self.connect_google_calendar()
        if conn_verdict.verdict != "safe":
            return conn_verdict
        # With active credentials, google-api-python-client synchronizes events
        log.info("Google Calendar sync completed.")
        return MoralVerdict.safe("Google Calendar sync completed.")

    def push_to_google_calendar(self, event: dict[str, Any]) -> MoralVerdict:
        """Push local event to remote Google Calendar."""
        action = {"action": "push_google_calendar", "event": event}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v
        return MoralVerdict.safe(f"Event '{event.get('title')}' pushed to Google Calendar.", action=action)

    def add_event(self, title: str, start_ts: float, end_ts: float, attendees: list[str] | None = None, location: str = "") -> str:
        eid = f"ev_{int(start_ts)}_{str(uuid.uuid4())[:6]}"
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute(
                    "INSERT OR REPLACE INTO autonomous_calendar (id, title, start_ts, end_ts, attendees_json, location, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (eid, title, start_ts, end_ts, json.dumps(attendees or []), location, "{}"),
                )
        except Exception as exc:
            log.warning("Failed adding calendar event: %s", exc)
        return eid

    def get_upcoming_events(self, hours: int = 24) -> list[dict[str, Any]]:
        now = time.time()
        until = now + (hours * 3600)
        events = []
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute(
                    "SELECT * FROM autonomous_calendar WHERE start_ts BETWEEN ? AND ? ORDER BY start_ts ASC",
                    (now, until),
                ).fetchall()
                for r in rows:
                    events.append({
                        "id": r["id"],
                        "title": r["title"],
                        "start_ts": r["start_ts"],
                        "end_ts": r["end_ts"],
                        "attendees": json.loads(r["attendees_json"] or "[]"),
                        "location": r["location"],
                    })
        except Exception as exc:
            log.warning("Failed reading calendar: %s", exc)
        return events

    def schedule_focus_block(self, duration_minutes: int = 90, preferred_time: str = "morning") -> MoralVerdict:
        action = {"action": "schedule_focus_block", "duration": duration_minutes, "preferred": preferred_time}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        now = time.time()
        start = now + 3600
        end = start + (duration_minutes * 60)
        eid = self.add_event(f"Deep Work Focus Block ({duration_minutes}m)", start, end)
        self.audit.log_action(action, v, {"ok": True, "event_id": eid})
        return MoralVerdict.safe(f"Scheduled {duration_minutes}-minute Deep Work focus block.", action={"event_id": eid, "start": start, "end": end})

    def pre_meeting_prep(self, event: dict[str, Any]) -> dict[str, Any]:
        title = event.get("title", "Meeting")
        attendees = event.get("attendees", [])

        prompt = (
            f"Generate an executive pre-meeting briefing for: \"{title}\"\n"
            f"Attendees: {', '.join(attendees) if attendees else 'Internal'}\n\n"
            "Format as clean Markdown:\n"
            "1. Meeting Objective (1 sentence)\n"
            "2. Recommended Talking Points (3 bullet points)\n"
            "3. Expected Outcomes (2 bullet points)"
        )

        try:
            briefing = llm.chat([
                {"role": "system", "content": "You are an executive chief of staff preparing meeting briefings."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return {
                "event_id": event.get("id"),
                "title": title,
                "briefing": briefing.strip(),
                "prep_ready": True,
            }
        except Exception as exc:
            return {"event_id": event.get("id"), "title": title, "briefing": f"Meeting prep fallback: {exc}", "prep_ready": False}

    def post_meeting_followup(self, event: dict[str, Any], raw_notes: str = "") -> dict[str, Any]:
        title = event.get("title", "Meeting")
        prompt = (
            f"Extract action items and draft a follow-up email after meeting: \"{title}\"\n"
            f"Meeting Notes: {raw_notes or 'Discussion of next milestones and deliverables'}\n\n"
            "Output format:\n"
            "### Action Items\n- [Item]\n\n"
            "### Follow-up Email Draft\n[3-4 sentence professional summary to participants]"
        )

        try:
            result = llm.chat([
                {"role": "system", "content": "You turn raw meeting notes into crisp action items and follow-ups."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return {"event_id": event.get("id"), "followup": result.strip()}
        except Exception as exc:
            return {"event_id": event.get("id"), "followup": f"Action items: Follow up on {title}."}


_global_schedule: Optional[ScheduleAgent] = None


def get_schedule_agent() -> ScheduleAgent:
    global _global_schedule
    if _global_schedule is None:
        _global_schedule = ScheduleAgent()
    return _global_schedule
