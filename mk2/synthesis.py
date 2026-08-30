"""Cross-Domain Synthesis Engine for EVO MK2 (JARVIS Phase 7 / Item 10).

Combines live data from Schedule, Email, Knowledge, Money, Security, and Wellness
subsystems into unified intelligence briefings and non-obvious dot-connections.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import llm

log = logging.getLogger("mk2.synthesis")


class SynthesisEngine:
    """Combines multi-domain EVO data into unified morning/evening intelligence."""

    def connect_dots(self, items: list[dict[str, Any]]) -> list[str]:
        """Find non-obvious, actionable connections across data sources."""
        if len(items) < 2:
            return []

        prompt = (
            "You are JARVIS connecting dots across calendar, email, knowledge, and money data:\n\n"
            + json.dumps(items, indent=2, default=str)
            + "\n\nIdentify 1-3 non-obvious, actionable connections. "
            "Example: 'Meeting tomorrow with Acme Corp + last email mentioned budget constraints + past proposal → prepare cost-saving option.'\n\n"
            "Return ONLY a bulleted list of insights."
        )

        try:
            out = llm.chat([
                {"role": "system", "content": "You are JARVIS anticipating unseen strategic connections."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            lines = [line.strip("- *").strip() for line in out.strip().split("\n") if line.strip()]
            return [l for l in lines if len(l) > 10][:3]
        except Exception as exc:
            log.warning("Dot connection error: %s", exc)
            return ["Review cross-domain timeline between upcoming calendar events and open communications."]

    def generate_briefing(self) -> str:
        """Generate a unified executive morning briefing from ALL subsystems."""
        try:
            from .schedule_agent import get_schedule_agent
            from .email_agent import get_email_agent
            from .money_engine import get_money_engine
            from .security_agent import get_security_agent
            from .wellness_agent import get_wellness_agent

            calendar = get_schedule_agent().get_upcoming_events(24)
            emails = get_email_agent().read_inbox(limit=5, filter="opportunities")
            opps = get_money_engine().scan_opportunities()
            security = get_security_agent().security_report()
            wellness = get_wellness_agent().track_screen_time()
        except Exception as exc:
            log.debug("Subsystem context gathering note: %s", exc)
            calendar, emails, opps, security, wellness = [], [], [], "Protected", {}

        prompt = f"""Generate a concise executive morning briefing for the user:

TODAY'S CALENDAR:
{json.dumps(calendar[:3], indent=2)}

URGENT / OPPORTUNITY EMAILS:
{json.dumps(emails[:3], indent=2)}

MONEY OPPORTUNITIES:
{json.dumps(opps[:2], indent=2)}

SECURITY STATUS:
{security}

WELLNESS:
{json.dumps(wellness, indent=2)}

Format as a clean, scannable briefing:
1. Today at a Glance (calendar + key items)
2. Money (opportunities & proposals)
3. Alerts (security & wellness)
4. Recommended Actions (3 specific things to do today)
"""

        try:
            res = llm.chat([
                {"role": "system", "content": "You are EVO preparing the daily executive briefing."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return res.strip()
        except Exception as exc:
            return f"Morning briefing fallback: Today's schedule includes {len(calendar)} events."

    def proactive_suggestion(self, context: str = "") -> str:
        """Generate a single high-value proactive suggestion based on current context."""
        prompt = f"Given current user context: \"{context or 'Active development on EVO MK2'}\", give ONE sharp, proactive recommendation (1 sentence)."
        try:
            res = llm.chat([
                {"role": "system", "content": "You are JARVIS providing sharp, anticipatory suggestions."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return res.strip()
        except Exception:
            return "Consider reviewing pending opportunities in the Approval Queue."


_global_synthesis: Optional[SynthesisEngine] = None


def get_synthesis_engine() -> SynthesisEngine:
    global _global_synthesis
    if _global_synthesis is None:
        _global_synthesis = SynthesisEngine()
    return _global_synthesis
