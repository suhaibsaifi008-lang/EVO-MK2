"""Reminder dispatcher: fires due reminders exactly once."""
from . import db


def tick(publish, now: float | None = None) -> int:
    """Fire all due reminders via publish(). Returns count fired."""
    fired = 0
    for r in db.reminders_due(now):
        db.reminder_mark_fired(r["id"])
        publish("notify.out", {"kind": "reminder", "text": f"Reminder: {r['text']}"})
        fired += 1
    return fired
