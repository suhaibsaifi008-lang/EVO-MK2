"""Proactive Communication Intelligence for EVO MK2 (JARVIS Phase 3).

Anticipates messaging needs, triages communications, detects thread staleness,
and filters urgent interruptions.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.comms_intelligence")


class CommsIntelligence:
    """Evaluates message triage, follow-up timing, and interruption criteria."""

    def __init__(self):
        self.ethics = get_moral_engine()

    def should_reply_now(self, email_or_msg: dict[str, Any]) -> MoralVerdict:
        """Determine if a communication should be addressed immediately or batched."""
        urgency = email_or_msg.get("urgency", 50)
        is_opp = email_or_msg.get("is_opportunity", False)
        subj = str(email_or_msg.get("subject", "")).lower()
        sender = str(email_or_msg.get("from", "")).lower()

        # Immediate reply candidates
        if urgency >= 80 or is_opp or any(w in subj for w in ("urgent", "asap", "invoice", "payment", "proposal accepted")):
            return MoralVerdict.safe("High urgency: reply recommended immediately.", action=email_or_msg)

        return MoralVerdict.safe("Normal priority: can be processed in next daily batch.", action=email_or_msg)

    def suggest_follow_up(self, thread: list[dict[str, Any]], days_silent: int = 3) -> Optional[dict[str, Any]]:
        """Identify cold threads where a client or contact has not replied."""
        if not thread:
            return None
        last_msg = thread[-1]
        last_ts = last_msg.get("ts", time.time())
        # If user sent the last message and no reply after days_silent
        if last_msg.get("from_user", True) and (time.time() - last_ts) >= (days_silent * 86400):
            return {
                "thread_id": thread[0].get("thread_id", "t-001"),
                "recipient": last_msg.get("to", "Client"),
                "days_silent": days_silent,
                "suggestion": f"Follow up with {last_msg.get('to')} regarding '{last_msg.get('subject', 'project')}'. Thread has been quiet for {days_silent} days.",
            }
        return None

    def detect_urgent(self, message: dict[str, Any]) -> bool:
        """Decide whether this message warrants interrupting the user."""
        text = (str(message.get("subject", "")) + " " + str(message.get("body", "") or message.get("text", ""))).lower()
        urgent_signals = ["server down", "production outage", "security alert", "fraud", "contract signed", "emergency", "deadline missed"]
        return any(s in text for s in urgent_signals) or message.get("urgency", 0) >= 90


_global_comms_intel: Optional[CommsIntelligence] = None


def get_comms_intelligence() -> CommsIntelligence:
    global _global_comms_intel
    if _global_comms_intel is None:
        _global_comms_intel = CommsIntelligence()
    return _global_comms_intel
