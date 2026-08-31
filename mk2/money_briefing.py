"""Daily Actionable Money Briefing for EVO MK2.

Compiles revenue milestones, active pipeline stages, pending invoices,
and outputs top 3 highest-ROI revenue actions for the day.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

from . import bus
from .crm import get_crm
from .invoicing import get_invoicing_engine
from .revenue import get_revenue_tracker

log = logging.getLogger("mk2.money_briefing")


class MoneyBriefingEngine:
    """Produces tactical daily financial & pipeline intelligence."""

    def __init__(self) -> None:
        self.crm = get_crm()
        self.revenue = get_revenue_tracker()
        self.invoicing = get_invoicing_engine()

    def generate_briefing(self) -> dict[str, Any]:
        """Compile live financial data and generate actionable Markdown briefing."""
        funnel_30d = self.revenue.get_funnel_metrics(days=30)
        pipeline = self.crm.get_pipeline_summary()
        invoices = self.invoicing.list_invoices()
        pending_invoices = [i for i in invoices if i.status in ("draft", "sent")]
        hot_leads = self.crm.list_clients(stage="in_discussion") + self.crm.list_clients(stage="pitched")

        # Derive Top 3 High-ROI Actions
        actions: list[str] = []
        if hot_leads:
            top_lead = hot_leads[0]
            actions.append(f"Follow up with **{top_lead.name}** (Budget: ${top_lead.budget:,.2f}, Score: {top_lead.lead_score:.0f}/100) — Stage: {top_lead.stage}")

        if pending_invoices:
            top_inv = pending_invoices[0]
            actions.append(f"Send payment reminder for **{top_inv.id}** (${top_inv.total_amount:,.2f} due from {top_inv.client_name})")

        actions.append("Scan Upwork & inbound channels for new high-budget Python/AI freelance contracts")
        if len(actions) < 3:
            actions.append("Review Gumroad digital product conversions & traffic sources")

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        total_rev = funnel_30d.get("total_revenue", 0.0)
        conversion_rate = funnel_30d.get("conversion_rate_pct", 0.0)

        md = f"""# Daily Money & Revenue Briefing
*{now_str} &middot; Autonomous Income Intelligence*

---

### Executive Financial Snapshot
- **30-Day Total Revenue:** ${total_rev:,.2f} USD
- **Pipeline Value:** ${pipeline.get('pipeline_value', 0.0):,.2f} USD across {pipeline.get('total_clients', 0)} tracked clients
- **Funnel Conversion Rate:** {conversion_rate:.1f}% ({funnel_30d.get('paid', 0)} completed / {funnel_30d.get('proposals_sent', 0)} pitched)

---

### Active Pipeline Breakdown
- **Leads:** {pipeline.get('stages', {}).get('lead', 0)}
- **Pitched / Proposals Out:** {pipeline.get('stages', {}).get('pitched', 0)}
- **In Active Discussion:** {pipeline.get('stages', {}).get('in_discussion', 0)}
- **Contracts Sent:** {pipeline.get('stages', {}).get('contract_sent', 0)}
- **Active Paying Clients:** {pipeline.get('stages', {}).get('active', 0)}

---

### Top 3 Recommended Revenue Actions Today
1. {actions[0]}
2. {actions[1]}
3. {actions[2] if len(actions) > 2 else 'Review active freelance platforms'}

---

### Pending Invoices & Collectibles
{chr(10).join(f"- **{i.id}** — {i.client_name}: ${i.total_amount:,.2f} ({i.status.upper()})" for i in pending_invoices[:5]) or "No pending invoices."}
"""

        # Save briefing note into vault
        note_path = None
        try:
            from .vault import write_note
            slug = f"money-briefing-{datetime.now().strftime('%Y-%m-%d')}"
            note_path = write_note(slug, md, tags=["money", "briefing", "revenue"])
        except Exception as exc:
            log.debug("Vault save note for money briefing: %s", exc)

        bus.publish("money.briefing_generated", {
            "timestamp": time.time(),
            "total_revenue": total_rev,
            "pipeline_value": pipeline.get("pipeline_value", 0.0),
            "top_actions": actions[:3],
        })

        return {
            "ok": True,
            "timestamp": time.time(),
            "markdown": md,
            "total_revenue": total_rev,
            "pipeline_value": pipeline.get("pipeline_value", 0.0),
            "top_actions": actions[:3],
            "vault_path": str(note_path) if note_path else "",
        }


_global_briefing: Optional[MoneyBriefingEngine] = None


def get_money_briefing_engine() -> MoneyBriefingEngine:
    global _global_briefing
    if _global_briefing is None:
        _global_briefing = MoneyBriefingEngine()
    return _global_briefing
