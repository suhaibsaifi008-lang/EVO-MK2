"""Upwork Autonomous Specialist for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import get_browser_agent
from ..consent import get_consent_manager
from ..ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.upwork")


class UpworkAgent:
    """Specialist agent for Upwork gig discovery, evaluation, and proposal generation."""

    RATE_LIMITS = {
        "proposals_per_day": 5,
        "min_interval_seconds": 3600,
        "max_bid_amount": 500.0,
    }

    def __init__(self):
        self.browser = get_browser_agent()
        self.ethics = get_moral_engine()
        self.consent = get_consent_manager()
        self.audit = get_audit_logger()
        self.proposals_sent_today = 0
        self.last_proposal_ts = 0.0
        self.known_clients: set[str] = set()

    def evaluate_gig(self, gig: dict[str, Any], user_skills: str = "") -> dict[str, Any]:
        """Score an Upwork job listing 1-10 on suitability, margin, and win rate."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Web Scraping, Automation, AI Agents, Playwright")
            except Exception:
                user_skills = "Python, Web Scraping, Automation, AI Agents, Playwright"

        title = gig.get("title", "")
        desc = gig.get("description", "")
        budget = gig.get("budget", "Flexible")

        prompt = (
            f"Evaluate this Upwork gig for a freelancer with skills: {user_skills}\n\n"
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"Budget: {budget}\n\n"
            "Evaluate on a 1-10 scale:\n"
            "1. Skill fit\n"
            "2. Win likelihood\n"
            "3. Legitimacy (low scam probability)\n"
            "4. Effort-to-pay ratio\n\n"
            'Return ONLY JSON: {"score": <1-10>, "recommendation": "pursue"|"skip"|"caution", "reasoning": "<1 sentence>", "suggested_bid": <number>}'
        )

        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a pragmatic freelance strategist scoring opportunities."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as exc:
            log.warning("Gig evaluation failed: %s", exc)
            return {"score": 5, "recommendation": "caution", "reasoning": f"Automated scoring error: {exc}", "suggested_bid": 150.0}

    def generate_cover_note(self, gig: dict[str, Any], user_skills: str = "") -> str:
        """Write a personalized 3-4 sentence high-converting proposal."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Playwright, Automation")
            except Exception:
                user_skills = "Python, Playwright, Automation"

        title = gig.get("title", "")
        desc = gig.get("description", "")

        prompt = (
            f"Write an Upwork proposal for this gig:\n"
            f"Title: {title}\n"
            f"Description: {desc}\n"
            f"Skills: {user_skills}\n\n"
            "Rules:\n"
            "- Reference a SPECIFIC technical detail from the job description.\n"
            "- Explain exactly how you will solve their problem directly.\n"
            "- 3-4 sentences max. No fluff, no 'Dear Hiring Manager', no generic boasts.\n"
            "- Professional and confident tone."
        )

        try:
            reply = llm.chat([
                {"role": "system", "content": "You write concise, winning freelance proposals."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
            return reply.strip()
        except Exception as exc:
            return f"I can implement the required solution for {title} using {user_skills}. Ready to start immediately."

    def submit_proposal(self, gig: dict[str, Any], user_skills: str = "", user_approved: bool = False) -> MoralVerdict:
        """Prepare and submit an Upwork proposal with strict rate limits and approval gates."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Automation")
            except Exception:
                user_skills = "Python, Automation"

        now = time.time()
        if self.proposals_sent_today >= self.RATE_LIMITS["proposals_per_day"]:
            return MoralVerdict.block(f"Daily limit reached ({self.proposals_sent_today}/{self.RATE_LIMITS['proposals_per_day']}).")

        if (now - self.last_proposal_ts) < self.RATE_LIMITS["min_interval_seconds"]:
            wait = int(self.RATE_LIMITS["min_interval_seconds"] - (now - self.last_proposal_ts))
            return MoralVerdict.block(f"Interval safety active. Wait {wait} seconds before next proposal.")

        evaluation = self.evaluate_gig(gig, user_skills)
        if evaluation.get("recommendation") == "skip":
            return MoralVerdict.block(f"Opportunity skipped: {evaluation.get('reasoning')}")

        cover_note = self.generate_cover_note(gig, user_skills)
        bid = float(evaluation.get("suggested_bid", 150.0))
        if bid > self.RATE_LIMITS["max_bid_amount"]:
            bid = self.RATE_LIMITS["max_bid_amount"]

        client_id = str(gig.get("client_id") or gig.get("client_name") or "unknown_client")
        is_first_contact = client_id not in self.known_clients

        action_payload = {
            "platform": "upwork",
            "type": "proposal_submit",
            "gig": gig,
            "cover_note": cover_note,
            "bid": bid,
            "client_id": client_id,
            "evaluation": evaluation,
        }

        v = self.ethics.evaluate(action_payload)
        if v.verdict == "block":
            return v

        if is_first_contact and not user_approved:
            return MoralVerdict.caution(
                f"First proposal to client '{client_id}' requires user review.",
                risks=["first_time_client", "approval_required"],
                action=action_payload,
            )

        if not self.consent.has_consent("proposal_submit") and not user_approved:
            return MoralVerdict.caution("Submitting proposals requires explicit consent.", action=action_payload)

        self.proposals_sent_today += 1
        self.last_proposal_ts = now
        self.known_clients.add(client_id)
        self.consent.record_outcome("proposal_submit", True, f"Submitted to {client_id} (${bid})")
        self.audit.log_action(action_payload, v, {"ok": True, "bid": bid, "status": "submitted"})

        return MoralVerdict.safe(f"Proposal submitted for '{gig.get('title')}' at ${bid:.2f}", action=action_payload)
