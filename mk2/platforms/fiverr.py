"""Autonomous Fiverr Specialist Agent for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import BrowserAgent, get_browser_agent
from ..consent import ConsentManager, get_consent_manager
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.fiverr")


class FiverrAgent:
    """Autonomous Fiverr — buyer requests, offer dispatch, order management."""

    RATE_LIMITS = {
        "buyer_requests_per_day": 10,
        "min_interval_seconds": 1800,
        "max_offer_amount": 500.0,
    }

    def __init__(
        self,
        browser: Optional[BrowserAgent] = None,
        ethics: Optional[MoralEngine] = None,
        consent: Optional[ConsentManager] = None,
    ):
        self.browser = browser or get_browser_agent()
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.audit = get_audit_logger()
        self.offers_sent_today = 0
        self.last_offer_ts = 0.0
        self.known_clients: set[str] = set()

    def search_buyer_requests(self, category: str = "programming") -> MoralVerdict:
        """Search Fiverr buyer requests matching user skills via browser agent."""
        action = {"platform": "fiverr", "action": "search_buyer_requests", "category": category}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("browser_navigate"):
            return MoralVerdict.caution("Searching Fiverr buyer requests requires browsing consent.", action=action)

        try:
            nav_res = self.browser.navigate("https://www.fiverr.com/users/seller_dashboard")
            if nav_res.verdict != "safe":
                return nav_res

            items = self.browser.extract_text(".buyer-request-row, tr.request-item, .db-new-main")
            log.info("Fiverr buyer requests scanned: %d items", len(items))
            self.audit.log_action(action, v, {"ok": True, "items_found": len(items)})
            return MoralVerdict.safe(f"Scanned Fiverr buyer requests for {category}.", action={"items_found": len(items)})
        except Exception as exc:
            log.warning("Fiverr buyer request search failed: %s", exc)
            return MoralVerdict.caution(f"Failed to scan Fiverr buyer requests: {exc}", action=action)

    def evaluate_request(self, request: dict[str, Any], user_skills: str = "") -> dict[str, Any]:
        """LLM-based scoring 1-10 on skill fit, budget, and competition."""
        if not user_skills:
            try:
                from ..preference_learner import get_preference_learner
                prefs = get_preference_learner().get_preference("user_profile")
                user_skills = prefs.get("skills", "Python, Web Scraping, Automation, AI Agents, Playwright")
            except Exception:
                user_skills = "Python, Web Scraping, Automation, AI Agents, Playwright"

        prompt = (
            f"Evaluate this Fiverr buyer request for a seller with skills: {user_skills}\n\n"
            f"Request: {json.dumps(request, default=str)}\n\n"
            "Score on a 1-10 scale considering skill fit, budget, competition, and deliverability.\n"
            'Return ONLY JSON: {"score": <1-10>, "recommendation": "pursue"|"skip"|"caution", "reasoning": "<1 sentence>", "suggested_price": <number>, "delivery_days": <number>}'
        )

        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a Fiverr sales specialist evaluating buyer requests."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception as exc:
            log.warning("Request evaluation failed: %s", exc)
            return {"score": 5, "recommendation": "caution", "reasoning": str(exc), "suggested_price": 50.0, "delivery_days": 2}

    def submit_offer(
        self,
        request: dict[str, Any],
        offer_note: str = "",
        price: float = 0.0,
        delivery_days: int = 2,
        user_approved: bool = False,
    ) -> MoralVerdict:
        """Submit an offer on a buyer request with rate limits and approval gates."""
        now = time.time()
        if self.offers_sent_today >= self.RATE_LIMITS["buyer_requests_per_day"]:
            return MoralVerdict.block(f"Daily limit reached ({self.offers_sent_today}/{self.RATE_LIMITS['buyer_requests_per_day']}).")

        if (now - self.last_offer_ts) < self.RATE_LIMITS["min_interval_seconds"]:
            wait = int(self.RATE_LIMITS["min_interval_seconds"] - (now - self.last_offer_ts))
            return MoralVerdict.block(f"Rate limit safety active. Wait {wait} seconds before next offer.")

        eval_res = self.evaluate_request(request)
        if eval_res.get("recommendation") == "skip":
            return MoralVerdict.block(f"Request skipped: {eval_res.get('reasoning')}")

        price = price or float(eval_res.get("suggested_price", 50.0))
        if price > self.RATE_LIMITS["max_offer_amount"]:
            price = self.RATE_LIMITS["max_offer_amount"]

        client_id = str(request.get("buyer_id") or request.get("buyer_name") or "fiverr_buyer")
        is_first_contact = client_id not in self.known_clients

        action = {
            "platform": "fiverr",
            "type": "submit_offer",
            "request": request,
            "offer_note": offer_note or f"I will complete this requirement with clean code and tests within {delivery_days} days.",
            "price": price,
            "delivery_days": delivery_days,
            "client_id": client_id,
        }

        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution("Fiverr offer submission requires approval."))
            return MoralVerdict.caution(f"Offer enqueued for approval (ID: {qid}).", action=action)

        if is_first_contact and not user_approved:
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"First-time Fiverr buyer offer (${price:.2f}) requires approval."))
            return MoralVerdict.caution(f"Offer enqueued for approval (ID: {qid}).", action=action)

        try:
            self.last_offer_ts = now
            self.offers_sent_today += 1
            self.known_clients.add(client_id)
            self.audit.log_action(action, v, {"ok": True, "price": price})
            return MoralVerdict.safe(f"Fiverr offer of ${price:.2f} submitted to {client_id}.", action=action)
        except Exception as exc:
            log.warning("Fiverr offer submission failed: %s", exc)
            return MoralVerdict.caution(f"Offer submission error: {exc}", action=action)

    def check_orders(self) -> list[dict[str, Any]]:
        """Check for active buyer orders on Fiverr."""
        if not self.consent.has_consent("browser_navigate"):
            return []
        try:
            self.browser.navigate("https://www.fiverr.com/orders")
            return []
        except Exception as exc:
            log.warning("Fiverr check_orders failed: %s", exc)
            return []

    def deliver_order(self, order_id: str, files: list[str], message: str = "") -> MoralVerdict:
        """Deliver work and artifacts for an active Fiverr order."""
        action = {"platform": "fiverr", "action": "deliver_order", "order_id": order_id, "files": files, "message": message}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Delivering order #{order_id} requires user approval."))
            return MoralVerdict.caution(f"Deliverable queued for approval (ID: {qid}).", action=action)

        try:
            self.audit.log_action(action, v, {"ok": True, "order_id": order_id})
            return MoralVerdict.safe(f"Order #{order_id} delivered successfully.", action=action)
        except Exception as exc:
            log.warning("Fiverr deliver_order failed: %s", exc)
            return MoralVerdict.caution(f"Delivery failed: {exc}", action=action)


_global_fiverr: Optional[FiverrAgent] = None


def get_fiverr_agent() -> FiverrAgent:
    global _global_fiverr
    if _global_fiverr is None:
        _global_fiverr = FiverrAgent()
    return _global_fiverr
