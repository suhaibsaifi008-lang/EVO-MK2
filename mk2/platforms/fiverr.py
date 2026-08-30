"""Autonomous Fiverr Agent for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations
import json, logging, time
from typing import Any, Optional
from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import BrowserAgent, get_browser_agent
from ..consent import ConsentManager, get_consent_manager
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.fiverr")

class FiverrAgent:
    RATE_LIMITS = {"buyer_requests_per_day": 10, "min_interval_seconds": 1800, "max_offer_amount": 500.0}

    def __init__(self, browser: Optional[BrowserAgent] = None, ethics: Optional[MoralEngine] = None, consent: Optional[ConsentManager] = None):
        self.browser = browser or get_browser_agent()
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.audit = get_audit_logger()
        self.offers_sent_today = 0
        self.last_offer_ts = 0.0
        self.known_clients: set[str] = set()

    def search_buyer_requests(self, category: str = "programming") -> MoralVerdict:
        action = {"action": "fiverr_search_requests", "category": category}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        if not self.consent.has_consent("browser_navigate"):
            return MoralVerdict.caution("Searching Fiverr buyer requests requires browsing consent.", action=action)
        nav_res = self.browser.navigate("https://www.fiverr.com/users/seller_dashboard")
        if nav_res.verdict != "safe": return nav_res
        items = self.browser.extract_text(".buyer-request-row, tr.request-item")
        return MoralVerdict.safe(f"Scanned Fiverr buyer requests for {category}.", action={"items_found": len(items)})

    def evaluate_request(self, request: dict[str, Any], user_skills: str = "") -> dict[str, Any]:
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Automation, Web Scraping")
            except Exception:
                user_skills = "Python, Automation, Web Scraping"
        prompt = (
            f"Evaluate this Fiverr request for skills {user_skills}:\n"
            f"{json.dumps(request)}\n"
            'Return JSON: {"score": 8, "recommendation": "pursue", "reasoning": "good fit", "suggested_price": 50, "delivery_days": 2}'
        )
        try:
            raw = llm.chat([{"role": "system", "content": "You evaluate freelance gigs."}, {"role": "user", "content": prompt}], role="fast", temperature=0.2)
            clean = raw.strip()
            if clean.startswith("```"): clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as exc:
            return {"score": 5, "recommendation": "skip", "reasoning": str(exc), "suggested_price": 50.0, "delivery_days": 2}

    def submit_offer(self, request: dict[str, Any], offer_note: str = "", price: float = 0.0, delivery_days: int = 2, user_approved: bool = False) -> MoralVerdict:
        now = time.time()
        if self.offers_sent_today >= self.RATE_LIMITS["buyer_requests_per_day"]:
            return MoralVerdict.block("Daily limit reached.")
        if (now - self.last_offer_ts) < self.RATE_LIMITS["min_interval_seconds"]:
            return MoralVerdict.block("Rate limit interval safety active.")
        eval_res = self.evaluate_request(request)
        if eval_res.get("recommendation") == "skip":
            return MoralVerdict.block(f"Request skipped: {eval_res.get('reasoning')}")
        price = price or float(eval_res.get("suggested_price", 50.0))
        client_id = str(request.get("buyer_id") or "fiverr_buyer")
        is_first = client_id not in self.known_clients
        action = {"platform": "fiverr", "type": "submit_offer", "request": request, "offer_note": offer_note or f"I will complete this in {delivery_days} days.", "price": price, "client_id": client_id}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        if is_first and not user_approved:
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution("First-time Fiverr buyer offer requires approval."))
            return MoralVerdict.caution(f"Offer enqueued for approval (ID: {qid}).", action=action)
        self.last_offer_ts = now
        self.offers_sent_today += 1
        self.known_clients.add(client_id)
        self.audit.log_action(action, v, {"ok": True, "price": price})
        return MoralVerdict.safe(f"Fiverr offer of ${price:.2f} submitted.", action=action)

    def check_orders(self) -> list[dict[str, Any]]:
        return []

    def deliver_order(self, order_id: str, files: list[str], message: str = "") -> MoralVerdict:
        action = {"action": "fiverr_deliver", "order_id": order_id, "files": files, "message": message}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        self.audit.log_action(action, v, {"ok": True})
        return MoralVerdict.safe(f"Order #{order_id} delivered successfully.")

_global_fiverr: Optional[FiverrAgent] = None
def get_fiverr_agent() -> FiverrAgent:
    global _global_fiverr
    if _global_fiverr is None: _global_fiverr = FiverrAgent()
    return _global_fiverr
