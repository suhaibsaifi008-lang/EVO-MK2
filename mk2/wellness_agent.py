"""Health & Wellness Intelligence Agent for EVO MK2 (JARVIS Phase 9)."""
from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Optional

from .ethics import MoralVerdict

log = logging.getLogger("mk2.wellness_agent")


class WellnessAgent:
    """Monitors screen time, suggests physical/mental breaks, and detects late-night stress."""

    def __init__(self):
        self.session_start_ts = time.time()
        self.last_break_ts = time.time()

    def track_screen_time(self) -> dict[str, Any]:
        now = time.time()
        current_session_minutes = round((now - self.session_start_ts) / 60, 1)
        time_since_break_minutes = round((now - self.last_break_ts) / 60, 1)
        return {
            "current_session_minutes": current_session_minutes,
            "minutes_since_last_break": time_since_break_minutes,
            "is_late_night": datetime.datetime.now().hour >= 23 or datetime.datetime.now().hour < 5,
        }

    def suggest_break(self) -> MoralVerdict:
        stats = self.track_screen_time()
        if stats["minutes_since_last_break"] >= 120:
            self.last_break_ts = time.time()
            return MoralVerdict.caution(
                f"You have been working continuously for {int(stats['minutes_since_last_break'])} minutes. Take a 5-minute break to rest your eyes.",
                risks=["eye_strain", "focus_fatigue"],
                action={"action": "suggest_break", "minutes": stats["minutes_since_last_break"]},
            )
        return MoralVerdict.safe("Screen time within healthy working limits.")

    def stress_detection(self, calendar_density: int = 0) -> MoralVerdict:
        stats = self.track_screen_time()
        if stats["is_late_night"]:
            return MoralVerdict.caution(
                "Late-night focus detected. Recommend saving your current state and resting to maintain peak cognitive sharpness.",
                risks=["sleep_deprivation", "burnout_risk"],
            )
        if calendar_density > 5:
            return MoralVerdict.caution(
                "High meeting load detected today. Recommend scheduling a 30-minute quiet focus block to decompress.",
                risks=["calendar_overload"],
            )
        return MoralVerdict.safe("Workload density appears balanced.")


_global_wellness: Optional[WellnessAgent] = None


def get_wellness_agent() -> WellnessAgent:
    global _global_wellness
    if _global_wellness is None:
        _global_wellness = WellnessAgent()
    return _global_wellness
