"""JARVIS Autonomous Decision Engine for EVO MK2 (JARVIS Phase 5).

Continuous strategic loop that scans platforms, morally evaluates opportunities,
ranks actions, routes to approval queue or autonomous execution, and learns from results.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

from . import llm
from .approval_queue import get_approval_queue
from .audit import get_audit_logger
from .consent import get_consent_manager
from .credential_vault import get_credential_vault
from .email_agent import get_email_agent
from .ethics import MoralVerdict, get_moral_engine
from .platforms.upwork import UpworkAgent
from .revenue import get_revenue_tracker

log = logging.getLogger("mk2.money_engine")


def _log_event(subsystem: str, event: str, **kwargs):
    log.info("[%s] %s %s", subsystem, event, " ".join(f"{k}={v}" for k, v in kwargs.items()))



class FunnelTracker:
    """Tracks stages: proposal_sent -> client_viewed -> client_responded -> hired -> delivered -> paid."""

    _KNOWN_STAGES = {
        "proposal_sent", "client_viewed", "client_responded", "hired", "delivered", "paid"
    }

    def __init__(self, revenue_tracker: Optional[Any] = None):
        self.revenue = revenue_tracker or get_revenue_tracker()

    def record_stage(self, stage: str = "proposal_sent", source: str = "general", client: str = "", amount: float = 0.0, meta: dict | None = None, **kwargs) -> int:
        """Record a funnel stage transition with positional-order protection."""
        s1 = str(stage or "").strip()
        s2 = str(source or "").strip()
        if s2 in self._KNOWN_STAGES and s1 not in self._KNOWN_STAGES:
            real_source = s1
            real_stage = s2
        else:
            real_stage = s1 if s1 in self._KNOWN_STAGES else (s1 or "proposal_sent")
            real_source = s2 or "general"

        if "action_type" in kwargs:
            real_stage = str(kwargs["action_type"])
        if "platform" in kwargs:
            real_source = str(kwargs["platform"])

        return self.revenue.record_action(
            source=real_source,
            action_type=real_stage,
            client=client,
            amount=amount,
            status=real_stage,
            meta=meta,
        )

    def get_funnel_metrics(self, days: int = 30) -> dict[str, Any]:
        return self.revenue.get_funnel_metrics(days)


class MoneyEngine:
    """Autonomous strategic engine managing income-generating opportunities."""

    def __init__(self, tick_interval: int = 3600):
        self.tick_interval = tick_interval
        self.running = False
        self._lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.vault = get_credential_vault()
        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.audit = get_audit_logger()
        self.revenue = get_revenue_tracker()
        self.funnel = FunnelTracker(self.revenue)
        self.queue = get_approval_queue()
        self.email = get_email_agent()
        self.upwork = UpworkAgent()
        from .financial_intelligence import get_financial_intelligence
        self.finance = get_financial_intelligence()
        self.last_tick_ts = 0.0

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True, name="EvoMoneyEngine")
            self.thread.start()
            log.info("MoneyEngine autonomous loop started (interval: %ds)", self.tick_interval)
            return True

    def stop(self) -> None:
        with self._lock:
            self.running = False
        log.info("MoneyEngine autonomous loop stopped.")

    def _loop(self) -> None:
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                log.error("MoneyEngine tick error: %s", exc)
                self.audit.log_action({"type": "engine_error", "error": str(exc)}, outcome={"ok": False})

            # Sleep in responsive increments without overshoot or infinite loops
            target = max(0.1, float(self.tick_interval)) if self.tick_interval > 0 else 1.0
            slept = 0.0
            while self.running and slept < target:
                step = min(0.5, target - slept)
                time.sleep(step)
                slept += step

    def tick(self) -> dict[str, Any]:
        """Run one cycle of opportunity discovery, evaluation, and proposal queueing."""
        self.last_tick_ts = time.time()
        log.info("MoneyEngine tick starting...")

        # 1. Scan configured platforms for opportunities
        opps = self.scan_opportunities()
        if not opps:
            log.info("MoneyEngine: No active opportunities discovered this cycle.")
            return {"ok": True, "opportunities_found": 0}

        # 2. Moral filtering
        safe_opps: list[dict[str, Any]] = []
        for opp in opps:
            verdict = self.ethics.evaluate(opp)
            if verdict.verdict == "safe":
                safe_opps.append(opp)
            elif verdict.verdict == "caution":
                # Queue questionable opportunity for human review
                self.queue.enqueue(opp, verdict)
            else:
                log.info("MoneyEngine moral block on opportunity: %s", verdict.reasoning)

        if not safe_opps:
            return {"ok": True, "opportunities_found": len(opps), "safe": 0}

        # 3. Strategic LLM Ranking
        from .llm_rate_limiter import get_llm_rate_limiter
        if not get_llm_rate_limiter().allow():
            log.warning("MoneyEngine tick skipped LLM ranking due to rate limit.")
            return {"ok": True, "skipped": "rate_limited"}

        best = self.pick_best_opportunity(safe_opps)
        if not best:
            return {"ok": True, "picked": None}

        # 4. Deep financial intelligence evaluation
        try:
            best["deep_evaluation"] = self.finance.evaluate_opportunity(best)
        except Exception as exc:
            log.debug("Financial intelligence evaluation note: %s", exc)

        # 5. Route: Auto-Approved vs Queue for Approval
        act_type = best.get("type", "opportunity")
        is_trusted = self.consent.is_auto_approved(act_type)

        if is_trusted and self.consent.has_consent(act_type):
            res = self.execute_opportunity(best)
            log.info("MoneyEngine executed auto-approved opportunity: %s", res)
            return {"ok": True, "executed": best, "result": res}
        else:
            verdict = MoralVerdict.caution(
                f"Opportunity requires user approval before execution ({best.get('title', 'Action')}).",
                risks=["unapproved_action"],
                action=best,
            )
            item_id = self.queue.enqueue(best, verdict)
            log.info("MoneyEngine enqueued opportunity #%s for user approval: %s", item_id, best.get("title"))
            return {"ok": True, "enqueued_id": item_id, "action": best}

    def scan_opportunities(self) -> list[dict[str, Any]]:
        """Gather potential gigs and outreach targets from all connected sources."""
        results: list[dict[str, Any]] = []

        # 1. Check Email inbox
        try:
            inbox_opps = self.email.read_inbox(limit=10, filter_opportunities=True)
            for m in inbox_opps:
                results.append({
                    "platform": "email",
                    "type": "email_opportunity",
                    "title": f"Inbound lead: {m.get('subject')}",
                    "from": m.get("from"),
                    "data": m,
                })
        except Exception as exc:
            log.debug("Email scan error: %s", exc)

        # 2. Check Upwork for live gig listings
        try:
            from .preference_learner import get_preference_learner
            prefs = get_preference_learner().get_preference("user_profile")
            skills = prefs.get("skills", "Python Automation")
            scrape_res = self.upwork.scrape_gigs(query=skills)
            if scrape_res.verdict == "safe" and scrape_res.action:
                gigs = scrape_res.action.get("gigs", [])
                for g in gigs:
                    results.append({
                        "platform": "upwork",
                        "type": "proposal_submit",
                        "title": g.get("title", "Upwork Opportunity"),
                        "budget": g.get("budget", "$100.00"),
                        "gig": g,
                        "evaluation": g.get("evaluation"),
                    })
        except Exception as exc:
            log.debug("Upwork scan error: %s", exc)

        return results

    def pick_best_opportunity(self, opportunities: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        """Use LLM reasoning to select the single highest-probability opportunity."""
        if len(opportunities) == 1:
            return opportunities[0]

        summary_list = []
        for i, op in enumerate(opportunities):
            summary_list.append(f"[{i}] Platform: {op.get('platform')}, Title: {op.get('title')}, Budget: {op.get('budget', 'N/A')}")

        prompt = (
            "Select the single best freelance or outreach opportunity with the highest probability "
            "of successful closing and payout within 7 days.\n\n"
            + "\n".join(summary_list)
            + '\n\nReturn ONLY JSON: {"pick": <index>, "reasoning": "<1 sentence>"}'
        )

        try:
            reply = llm.chat([
                {"role": "system", "content": "You are a freelance business strategist."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            clean = reply.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            choice = json.loads(clean.strip())
            idx = int(choice.get("pick", 0))
            if 0 <= idx < len(opportunities):
                return opportunities[idx]
            log.warning("LLM opportunity picker returned out-of-range index %d (total: %d)", idx, len(opportunities))
        except Exception as exc:
            log.warning("LLM opportunity picker failed: %s; selecting highest-value candidate", exc)

        # Fallback: rank by expected payout instead of blindly returning opportunities[0]
        sorted_opps = sorted(opportunities, key=lambda o: float(o.get("bid", 0) or o.get("budget", 0) or 0), reverse=True)
        fallback_pick = sorted_opps[0]
        log.info("Selected fallback opportunity by expected payout: '%s' ($%s)", fallback_pick.get("title", ""), fallback_pick.get("bid", fallback_pick.get("budget", 0)))
        return fallback_pick

    def execute_opportunity(self, opportunity: dict[str, Any]) -> dict[str, Any]:
        """Execute the opportunity through the respective platform agent."""
        plat = opportunity.get("platform")
        if plat == "upwork":
            v = self.upwork.submit_proposal(opportunity, user_approved=False)
            if v.verdict == "caution":
                try:
                    from .approval_queue import get_approval_queue
                    item_id = get_approval_queue().enqueue(
                        action={"type": "upwork_proposal", "opportunity": opportunity},
                        verdict=v,
                    )
                    res = v.to_dict()
                    res["approval_item_id"] = item_id
                    return res
                except Exception:
                    pass
            if v.verdict == "safe":
                self.funnel.record_stage(
                    stage="proposal_sent",
                    source="upwork",
                    client=opportunity.get("client_id", ""),
                    amount=float(opportunity.get("bid", 150.0)),
                    meta={"gig": opportunity.get("title")},
                )
            return v.to_dict()
        elif plat == "email":
            # Draft and log
            self.funnel.record_stage(
                stage="proposal_sent",
                source="email",
                client=opportunity.get("from", ""),
                amount=0.0,
                meta={"subject": opportunity.get("title")},
            )
            return {"ok": True, "status": "reviewed"}
        return {"ok": False, "error": f"Unknown platform: {plat}"}

    def get_funnel_metrics(self, days: int = 30) -> dict[str, Any]:
        """Return funnel conversion metrics across all monetization channels."""
        return self.funnel.get_funnel_metrics(days)


_global_money: Optional[MoneyEngine] = None


def get_money_engine() -> MoneyEngine:
    global _global_money
    if _global_money is None:
        _global_money = MoneyEngine()
    return _global_money
