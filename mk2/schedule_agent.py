"""Scheduling & Calendar Intelligence for EVO MK2 (JARVIS Phase 5 / Task 6).

Proactively manages user time: Google Calendar bidirectional sync, focus blocks,
pre-meeting dossiers, and post-meeting follow-ups.
"""
from __future__ import annotations

import datetime
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
        self._gcal_service = None
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
        try:
            creds_data = self.vault.get("google_calendar")
            if not creds_data:
                return MoralVerdict.caution(
                    "No Google Calendar credentials stored. Add credentials via vault.store('google_calendar', {...}). "
                    "Falling back to local SQLite calendar."
                )

            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build

                creds = Credentials(
                    token=creds_data.get("token"),
                    refresh_token=creds_data.get("refresh_token"),
                    token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
                    client_id=creds_data.get("client_id"),
                    client_secret=creds_data.get("client_secret"),
                    scopes=creds_data.get("scopes", ["https://www.googleapis.com/auth/calendar"]),
                )
                self._gcal_service = build("calendar", "v3", credentials=creds)
                return MoralVerdict.safe("Google Calendar service connected successfully.")
            except ImportError:
                return MoralVerdict.caution("google-api-python-client or google-auth not installed.")
        except Exception as exc:
            log.warning("Failed connecting to Google Calendar: %s", exc)
            return MoralVerdict.caution(f"Google Calendar connection failed: {exc}")

    def sync_google_calendar(self) -> MoralVerdict:
        """Pull remote Google Calendar events into local cache."""
        conn_verdict = self.connect_google_calendar()
        if conn_verdict.verdict != "safe" or not self._gcal_service:
            return conn_verdict

        try:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            events_result = self._gcal_service.events().list(
                calendarId="primary",
                timeMin=now_iso,
                maxResults=50,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            items = events_result.get("items", [])

            synced_count = 0
            for item in items:
                start_dt = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
                end_dt = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
                if not start_dt:
                    continue

                try:
                    start_ts = datetime.datetime.fromisoformat(start_dt.replace("Z", "+00:00")).timestamp()
                    end_ts = datetime.datetime.fromisoformat(end_dt.replace("Z", "+00:00")).timestamp() if end_dt else start_ts + 3600
                except Exception:
                    start_ts = time.time()
                    end_ts = start_ts + 3600

                title = item.get("summary", "Untitled Meeting")
                location = item.get("location", "")
                attendees = [a.get("email") for a in item.get("attendees", []) if a.get("email")]

                self.add_event(title, start_ts, end_ts, attendees, location)
                synced_count += 1

            self.audit.log_action({"action": "sync_google_calendar"}, MoralVerdict.safe(), {"events_synced": synced_count})
            return MoralVerdict.safe(f"Synchronized {synced_count} events from Google Calendar.", action={"synced": synced_count})
        except Exception as exc:
            log.warning("Google Calendar sync failed: %s", exc)
            return MoralVerdict.caution(f"Google Calendar sync failed: {exc}")

    def push_to_google_calendar(self, event: dict[str, Any]) -> MoralVerdict:
        """Push local event to remote Google Calendar."""
        action = {"action": "push_google_calendar", "event": event}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from .approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Adding event '{event.get('title')}' to Google Calendar requires approval."))
            return MoralVerdict.caution(f"Calendar event queued for approval (ID: {qid}).", action=action)

        if self._gcal_service:
            try:
                start_iso = datetime.datetime.fromtimestamp(event.get("start_ts", time.time()), tz=datetime.timezone.utc).isoformat()
                end_iso = datetime.datetime.fromtimestamp(event.get("end_ts", time.time() + 3600), tz=datetime.timezone.utc).isoformat()
                body = {
                    "summary": event.get("title", "Event"),
                    "location": event.get("location", ""),
                    "description": event.get("description", "Scheduled by EVO MK2"),
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                    "attendees": [{"email": a} for a in event.get("attendees", [])],
                }
                res = self._gcal_service.events().insert(calendarId="primary", body=body).execute()
                self.audit.log_action(action, v, {"ok": True, "gcal_id": res.get("id")})
                return MoralVerdict.safe(f"Event '{event.get('title')}' pushed to Google Calendar (ID: {res.get('id')}).", action={"gcal_id": res.get("id")})
            except Exception as exc:
                log.warning("Failed pushing event to Google Calendar API: %s", exc)
                return MoralVerdict.caution(f"Failed to push to Google Calendar: {exc}")

        # Local fallback record
        eid = self.add_event(event.get("title", "Event"), event.get("start_ts", time.time()), event.get("end_ts", time.time() + 3600), event.get("attendees", []), event.get("location", ""))
        self.audit.log_action(action, v, {"ok": True, "local_id": eid})
        return MoralVerdict.safe(f"Event '{event.get('title')}' scheduled in local calendar.", action={"local_id": eid})

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
