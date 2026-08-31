"""Unified Money Intelligence Engine for EVO MK2.

Provides rich real-time financial context snapshots directly into the LLM system prompt,
enabling deep strategic business reasoning, pipeline synthesis, and tactical monetization
recommendations without requiring multi-step tool calls for analysis.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .crm import get_crm
from .followup_engine import get_followup_engine
from .invoicing import get_invoicing_engine
from .money_briefing import get_money_briefing_engine
from .opportunity_scorer import get_opportunity_scorer
from .revenue import get_revenue_tracker

log = logging.getLogger("mk2.money_intelligence")


class MoneyIntelligence:
    """Consolidates CRM, billing, opportunities, and analytics into strategic business intelligence."""

    def __init__(self) -> None:
        self.crm = get_crm()
        self.revenue = get_revenue_tracker()
        self.scorer = get_opportunity_scorer()
        self.briefing = get_money_briefing_engine()
        self.invoicing = get_invoicing_engine()
        self.followups = get_followup_engine()

    def get_context(self) -> str:
        """Build a comprehensive, dense financial & business context string for the LLM."""
        funnel_30d = self.revenue.get_funnel_metrics(days=30)
        pipeline = self.crm.get_pipeline_summary()
        all_clients = self.crm.list_clients()
        invoices = self.invoicing.list_invoices()
        pending_invoices = [i for i in invoices if i.status in ("draft", "sent")]
        urgent_followups = self.followups.get_pending_followups()

        total_rev_30d = funnel_30d.get("total_revenue", 0.0)
        proposals_sent = funnel_30d.get("proposals_sent", 0)
        wins = funnel_30d.get("paid", 0)
        conv_rate = funnel_30d.get("conversion_rate_pct", 0.0)
        pipeline_val = pipeline.get("pipeline_value", 0.0)

        # Expected close value based on scores
        active_leads = [c for c in all_clients if c.stage in ("pitched", "in_discussion", "contract_sent")]
        weighted_pipeline = sum(c.budget * (c.lead_score / 100.0) for c in active_leads)

        # Top active clients
        client_lines = []
        for c in active_leads[:5]:
            client_lines.append(f"  * {c.name} ({c.platform}): ${c.budget:,.2f} [Stage: {c.stage}, Score: {c.lead_score:.0f}/100]")

        # Pending invoices
        inv_lines = []
        for inv in pending_invoices[:4]:
            inv_lines.append(f"  * {inv.id} for {inv.client_name}: ${inv.total_amount:,.2f} ({inv.status})")

        # Top follow-up nudges
        fu_lines = []
        for fu in urgent_followups[:3]:
            fu_lines.append(f"  * {fu.client_name} ({fu.cadence} cadence, {fu.days_since_update}d stale): Stage '{fu.recommended_stage}'")

        # Top 3 High-ROI Actions from briefing
        b_res = self.briefing.generate_briefing()
        actions = b_res.get("top_actions", [])

        ctx_parts = [
            "### FINANCIAL & PIPELINE SNAPSHOT",
            f"- 30-Day Revenue: ${total_rev_30d:,.2f} USD | Proposals Sent: {proposals_sent} | Contracts Won: {wins} ({conv_rate:.1f}% win rate)",
            f"- Active Pipeline Value: ${pipeline_val:,.2f} USD (Weighted Expected Value: ${weighted_pipeline:,.2f} USD across {len(active_leads)} active leads)",
            f"- Total Tracked Clients: {len(all_clients)} (Stages: {pipeline.get('stages', {})})",
            "",
            "### ACTIVE LEADS & DEALS IN FLIGHT:",
            "\n".join(client_lines) if client_lines else "  * No active leads in discussion.",
            "",
            "### PENDING INVOICES & RECEIVABLES:",
            "\n".join(inv_lines) if inv_lines else "  * No unpaid invoices pending.",
            "",
            "### CRITICAL FOLLOW-UPS NEEDED:",
            "\n".join(fu_lines) if fu_lines else "  * All client discussions are current.",
            "",
            "### TOP 3 RECOMMENDED REVENUE ACTIONS TODAY:",
            f"1. {actions[0]}" if len(actions) > 0 else "1. Scan Upwork for high-margin Python/AI development jobs",
            f"2. {actions[1]}" if len(actions) > 1 else "2. Follow up on open proposals",
            f"3. {actions[2]}" if len(actions) > 2 else "3. Review digital product revenue channels",
        ]

        return "\n".join(ctx_parts)

    def answer(self, question: str) -> str:
        """Answer high-level financial, pipeline, and monetization queries directly using rich business context."""
        from . import llm
        context_str = self.get_context()
        system_prompt = (
            "You are EVO MK2, the user's autonomous personal intelligence and Chief Revenue Officer.\n"
            "CRITICAL IDENTITY DIRECTIVE: You are EVO MK2. You were created and built by the user. "
            "NEVER claim to be Claude, Anthropic, or an AI with no opinions. NEVER give canned disclaimers about being unable to earn money or give financial thoughts. "
            "You have direct real-time access to the user's business pipeline, CRM, revenue, and active opportunities:\n\n"
            f"{context_str}\n\n"
            "Reason directly from this live data to answer the user's business question with specific numbers, "
            "actionable insights, and clear strategic recommendations. Do NOT pretend to look up data or call tools for analysis — you already know the business state."
        )
        return llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ], role="primary", temperature=0.3)


_global_money_intel: Optional[MoneyIntelligence] = None


def get_money_intelligence() -> MoneyIntelligence:
    global _global_money_intel
    if _global_money_intel is None:
        _global_money_intel = MoneyIntelligence()
    return _global_money_intel
