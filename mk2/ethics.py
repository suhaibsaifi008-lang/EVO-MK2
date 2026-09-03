"""Moral Reasoning Engine for EVO MK2 (JARVIS Foundation).

Evaluates actions before execution for potential harm, reputation risk, legal compliance,
and user safety. Categorizes actions into 'safe', 'caution' (requires approval), or 'block'.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("mk2.ethics")


@dataclass
class MoralVerdict:
    verdict: str  # "safe" | "caution" | "block"
    reasoning: str
    risks: list[str] = field(default_factory=list)
    action: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def safe(cls, reasoning: str = "Action evaluated as safe to execute.", action: dict | None = None) -> MoralVerdict:
        return cls(verdict="safe", reasoning=reasoning, risks=[], action=action or {})

    @classmethod
    def caution(cls, reasoning: str, risks: list[str] | None = None, action: dict | None = None) -> MoralVerdict:
        return cls(verdict="caution", reasoning=reasoning, risks=risks or [], action=action or {})

    @classmethod
    def block(cls, reasoning: str, risks: list[str] | None = None, action: dict | None = None) -> MoralVerdict:
        return cls(verdict="block", reasoning=reasoning, risks=risks or [], action=action or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reasoning": self.reasoning,
            "risks": self.risks,
            "action": self.action,
        }


class MoralEngine:
    """Evaluates potential autonomous actions before execution."""

    # Explicit hard-blocks: zero tolerance
    HARD_BLOCK_PATTERNS = [
        (r"\b(fake reviews?|fake ratings?|buy followers|fake testimonials?)\b", "Violates platform terms of service and compromises integrity."),
        (r"\b(password dump|credential leak|steal cookies|exfiltrate data)\b", "High security and privacy risk."),
        (r"\b(mass email|email blast|scrape emails|unsolicited spam|spam 50)\b", "High probability of triggering spam blacklists and damaging reputation."),
        (r"\b(crypto drainer|wallet transfer|wire money|unauthorized payment)\b", "Critical financial risk."),
    ]

    def evaluate(self, action: dict[str, Any], context: dict[str, Any] | None = None) -> MoralVerdict:
        """Evaluate an action before execution."""
        ctx = context or {}
        action_type = str(action.get("type") or action.get("action") or "").lower()
        desc = json.dumps(action, default=str).lower()

        # 1. Fast deterministic hard block check
        for pattern, reason in self.HARD_BLOCK_PATTERNS:
            if re.search(pattern, desc):
                log.warning("MoralEngine HARD BLOCK triggered: %s", reason)
                return MoralVerdict.block(reason, risks=["policy_violation", "reputation_damage"], action=action)

        # 2. Category-specific evaluation
        if "email" in action_type or "mail" in action_type:
            return self._evaluate_email(action, ctx)
        elif "proposal" in action_type or "upwork" in action_type or "fiverr" in action_type:
            return self._evaluate_gig_proposal(action, ctx)
        elif "browser" in action_type or "click" in action_type or "type" in action_type:
            return self._evaluate_browser_action(action, ctx)
        elif "financial" in action_type or "payment" in action_type or "money" in action_type:
            return self._evaluate_financial(action, ctx)

        # 3. Default safe for read/research actions
        if action_type in ("read", "search", "web_search", "screen_read", "weather", "vault_read"):
            return MoralVerdict.safe("Information retrieval action carries no external risk.", action=action)

        return MoralVerdict.caution("Action type unclassified; requires operator confirmation.", risks=["unclassified_action"], action=action)

    def _evaluate_email(self, action: dict[str, Any], context: dict[str, Any]) -> MoralVerdict:
        recipient = str(action.get("to") or "")
        subject = str(action.get("subject") or "")
        body = str(action.get("body") or action.get("context") or "")

        # Volume or spam filter check
        if any(w in body.lower() for w in ("guarantee 100%", "click here now", "urgent business", "dear sir/madam")):
            return MoralVerdict.caution(
                "Email contains generic or spammy phrasing that could harm deliverability.",
                risks=["spam_filter_risk"],
                action=action,
            )

        # Check if recipient looks valid
        if "@" not in recipient:
            return MoralVerdict.block("Invalid recipient email address.", risks=["invalid_recipient"], action=action)

        return MoralVerdict.safe("Email message is professional and targeted.", action=action)

    def _evaluate_gig_proposal(self, action: dict[str, Any], context: dict[str, Any]) -> MoralVerdict:
        rate = action.get("rate")
        try:
            rate_val = float(rate) if rate else 0.0
        except (ValueError, TypeError):
            rate_val = 0.0

        if rate_val > 1000.0:
            return MoralVerdict.caution(
                f"High-value proposal (${rate_val:.2f}) requires human confirmation.",
                risks=["high_financial_stakes"],
                action=action,
            )

        return MoralVerdict.safe("Proposal fits target criteria and standard pricing.", action=action)

    def _evaluate_browser_action(self, action: dict[str, Any], context: dict[str, Any]) -> MoralVerdict:
        url = str(action.get("url") or "").lower()
        if any(bad in url for bad in ("payment", "checkout", "delete", "deactivate", "settings/security")):
            return MoralVerdict.caution(
                f"Browser action targeting sensitive page ({url}). Confirmation required.",
                risks=["sensitive_page_interaction"],
                action=action,
            )
        return MoralVerdict.safe("Browser navigation is non-destructive.", action=action)

    def _evaluate_financial(self, action: dict[str, Any], context: dict[str, Any]) -> MoralVerdict:
        amount = action.get("amount", 0.0)
        try:
            amt = float(amount)
        except (ValueError, TypeError):
            amt = 0.0

        if amt > 0:
            return MoralVerdict.caution(
                f"Financial transaction of ${amt:.2f} requires user authorization.",
                risks=["financial_outflow"],
                action=action,
            )
        return MoralVerdict.safe("Financial record or query is read-only.", action=action)


_global_ethics: Optional[MoralEngine] = None


def get_moral_engine() -> MoralEngine:
    global _global_ethics
    if _global_ethics is None:
        _global_ethics = MoralEngine()
    return _global_ethics
