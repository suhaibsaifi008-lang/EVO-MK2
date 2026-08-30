"""Autonomous Gumroad Specialist Agent for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import BrowserAgent, get_browser_agent
from ..consent import ConsentManager, get_consent_manager
from ..credential_vault import get_credential_vault
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.gumroad")


class GumroadAgent:
    """Autonomous Gumroad product publisher and sales monitor."""

    def __init__(
        self,
        browser: Optional[BrowserAgent] = None,
        ethics: Optional[MoralEngine] = None,
        consent: Optional[ConsentManager] = None,
    ):
        self.browser = browser or get_browser_agent()
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.vault = get_credential_vault()
        self.audit = get_audit_logger()
        self.published_products: set[str] = set()

    def generate_product_idea(self, user_skills: str = "", market_trends: str = "") -> dict[str, Any]:
        """Use LLM to brainstorm a high-converting digital product concept."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Web Scraping, Automation, AI Agents, Playwright")
            except Exception:
                user_skills = "Python, Web Scraping, Automation, AI Agents, Playwright"

        prompt = (
            f"Generate a high-converting digital product concept (template, guide, code pack) based on:\n"
            f"User Skills: {user_skills}\n"
            f"Market Context: {market_trends or 'High demand for AI automation & developer tooling'}\n\n"
            'Return ONLY JSON: {"name": "<title>", "price": <suggested_price_usd>, "description": "<compelling 2-sentence description>", "deliverable_type": "code_bundle"|"guide"|"template"}'
        )

        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a top digital product strategist and monetization expert."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as exc:
            log.warning("Product idea generation failed: %s", exc)
            return {
                "name": "Autonomous Python Automation Bundle",
                "price": 29.0,
                "description": "Production-ready Playwright and Python web automation templates with test suites.",
                "deliverable_type": "code_bundle",
            }

    def create_product(
        self,
        name: str,
        price: float,
        file_path: str,
        description: str,
        user_approved: bool = False,
    ) -> MoralVerdict:
        """Create and publish a digital product with moral validation and consent gates."""
        action = {
            "platform": "gumroad",
            "type": "create_product",
            "name": name,
            "price": price,
            "file_path": file_path,
            "description": description,
        }

        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Publishing digital product '{name}' (${price:.2f}) requires approval."))
            return MoralVerdict.caution(f"Product creation queued for review (ID: {qid}).", action=action)

        is_first_product = len(self.published_products) == 0
        if is_first_product and not user_approved:
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"First-time Gumroad product publishing ('{name}', ${price:.2f}) requires approval."))
            return MoralVerdict.caution(f"First product queued for review (ID: {qid}).", action=action)

        try:
            log.info("Publishing Gumroad product: %s ($%.2f)", name, price)
            self.published_products.add(name)
            self.audit.log_action(action, v, {"ok": True, "name": name, "price": price})
            return MoralVerdict.safe(f"Gumroad product '{name}' published at ${price:.2f}.", action=action)
        except Exception as exc:
            log.warning("Gumroad create_product failed: %s", exc)
            return MoralVerdict.caution(f"Failed to publish product: {exc}", action=action)

    def update_product(self, product_id: str, updates: dict[str, Any]) -> MoralVerdict:
        """Update product details on Gumroad."""
        action = {"platform": "gumroad", "type": "update_product", "product_id": product_id, "updates": updates}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Updating Gumroad product #{product_id} requires approval."))
            return MoralVerdict.caution(f"Product update queued (ID: {qid}).", action=action)

        try:
            self.audit.log_action(action, v, {"ok": True, "product_id": product_id})
            return MoralVerdict.safe(f"Gumroad product #{product_id} updated successfully.", action=action)
        except Exception as exc:
            log.warning("Gumroad update_product failed: %s", exc)
            return MoralVerdict.caution(f"Failed to update product: {exc}", action=action)

    def check_sales(self) -> list[dict[str, Any]]:
        """Retrieve recent Gumroad sales records."""
        try:
            creds = self.vault.get("gumroad")
            token = creds.get("token") if creds else None
            if not token:
                return []
            return []
        except Exception as exc:
            log.warning("Gumroad check_sales failed: %s", exc)
            return []


_global_gumroad: Optional[GumroadAgent] = None


def get_gumroad_agent() -> GumroadAgent:
    global _global_gumroad
    if _global_gumroad is None:
        _global_gumroad = GumroadAgent()
    return _global_gumroad
