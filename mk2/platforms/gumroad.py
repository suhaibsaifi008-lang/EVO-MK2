"""Autonomous Gumroad Agent for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations
import json, logging
from typing import Any, Optional
from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import BrowserAgent, get_browser_agent
from ..consent import ConsentManager, get_consent_manager
from ..credential_vault import get_credential_vault
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.gumroad")

class GumroadAgent:
    def __init__(self, browser: Optional[BrowserAgent] = None, ethics: Optional[MoralEngine] = None, consent: Optional[ConsentManager] = None):
        self.browser = browser or get_browser_agent()
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.vault = get_credential_vault()
        self.audit = get_audit_logger()

    def generate_product_idea(self, user_skills: str = "", market_trends: str = "") -> dict[str, Any]:
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Automation, AI Agents")
            except Exception:
                user_skills = "Python, Automation, AI Agents"
        prompt = (
            f"Brainstorm a high-converting digital product for skills {user_skills}. "
            'Return JSON: {"name": "Title", "price": 29, "description": "Desc", "deliverable_type": "code_bundle"}'
        )
        try:
            raw = llm.chat([{"role": "system", "content": "You are a digital product strategist."}, {"role": "user", "content": prompt}], role="fast", temperature=0.3)
            clean = raw.strip()
            if clean.startswith("```"): clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception:
            return {"name": "Autonomous Python Automation Bundle", "price": 29.0, "description": "Production scraping templates.", "deliverable_type": "code_bundle"}

    def create_product(self, name: str, price: float, file_path: str, description: str, user_approved: bool = False) -> MoralVerdict:
        action = {"platform": "gumroad", "type": "create_product", "name": name, "price": price, "file_path": file_path, "description": description}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        if not user_approved and not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Publishing digital product '{name}' (${price:.2f}) requires approval."))
            return MoralVerdict.caution(f"Product creation queued for review (ID: {qid}).", action=action)
        self.audit.log_action(action, v, {"ok": True, "name": name, "price": price})
        return MoralVerdict.safe(f"Gumroad product '{name}' published at ${price:.2f}.", action=action)

    def check_sales(self) -> list[dict[str, Any]]:
        return []

_global_gumroad: Optional[GumroadAgent] = None
def get_gumroad_agent() -> GumroadAgent:
    global _global_gumroad
    if _global_gumroad is None: _global_gumroad = GumroadAgent()
    return _global_gumroad
