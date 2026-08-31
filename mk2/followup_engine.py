"""Automated Client Follow-up Engine for EVO MK2.

Identifies dormant proposals and discussions, scheduling high-conversion,
value-add follow-up drafts at optimal cadences (24h, 72h, 7 days).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .crm import Client, get_crm

log = logging.getLogger("mk2.followup_engine")


@dataclass
class FollowUpAction:
    client_id: str
    client_name: str
    client_email: str
    days_since_update: float
    recommended_stage: str
    draft_message: str
    cadence: str  # 24h, 72h, 7d


class FollowupEngine:
    """Detects when to nudge clients with non-spammy personalized messages."""

    def __init__(self) -> None:
        self.crm = get_crm()

    def get_pending_followups(self) -> list[FollowUpAction]:
        """Scan CRM for clients needing timely follow-ups."""
        actions: list[FollowUpAction] = []
        now = time.time()

        for client in self.crm.list_clients():
            if client.stage not in ("pitched", "in_discussion", "contract_sent"):
                continue

            elapsed_days = (now - client.updated_at) / 86400.0
            if elapsed_days >= 7.0:
                cadence = "7d"
            elif elapsed_days >= 3.0:
                cadence = "72h"
            elif elapsed_days >= 1.0 and client.stage == "in_discussion":
                cadence = "24h"
            else:
                continue

            draft = self.generate_followup_draft(client, cadence)
            actions.append(
                FollowUpAction(
                    client_id=client.id,
                    client_name=client.name,
                    client_email=client.email,
                    days_since_update=round(elapsed_days, 1),
                    recommended_stage=client.stage,
                    draft_message=draft,
                    cadence=cadence,
                )
            )

        return sorted(actions, key=lambda a: a.days_since_update, reverse=True)

    def generate_followup_draft(self, client: Client, cadence: str) -> str:
        """Create a polite, value-first follow-up message."""
        first_name = client.name.split()[0] if client.name else "there"

        if cadence == "24h":
            return (
                f"Hi {first_name}, following up on our discussion yesterday. "
                f"I've mapped out the initial architecture for your project and can get started as soon as we finalize scope. "
                f"Let me know if you have any questions or if you'd like to proceed!"
            )
        elif cadence == "72h":
            return (
                f"Hi {first_name}, just wanted to check in on the proposal I sent over. "
                f"Happy to adjust the timeline or milestone breakdown to best fit your goals. "
                f"Looking forward to collaborating!"
            )
        else:  # 7d
            return (
                f"Hi {first_name}, hope you're having a great week. "
                f"Circling back to see if you're still looking to kick off this project. "
                f"If priorities have shifted, no problem at all — let me know if we should revisit later."
            )

    def record_followup_sent(self, client_name: str, message: str) -> None:
        """Log follow-up event into CRM interaction timeline."""
        self.crm.record_interaction(client_name, "followup", f"Sent {message[:100]}...", {"full_message": message})


_global_followup: Optional[FollowupEngine] = None


def get_followup_engine() -> FollowupEngine:
    global _global_followup
    if _global_followup is None:
        _global_followup = FollowupEngine()
    return _global_followup
