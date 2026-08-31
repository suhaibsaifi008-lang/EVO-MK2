"""Invoicing & Proposal Engine for EVO MK2.

Generates professional client proposals, contracts, and itemized invoices from templates.
Tracks payment status and seamlessly updates CRM.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import DATA
from .crm import get_crm

log = logging.getLogger("mk2.invoicing")

INVOICE_DIR = DATA / "invoices"
INVOICE_DIR.mkdir(parents=True, exist_ok=True)
INVOICES_DB = INVOICE_DIR / "invoices.json"


@dataclass
class LineItem:
    description: str
    quantity: float = 1.0
    unit_price: float = 0.0
    total: float = 0.0

    def __post_init__(self):
        if self.total == 0.0:
            self.total = round(self.quantity * self.unit_price, 2)


@dataclass
class Invoice:
    id: str
    client_name: str
    client_email: str
    items: list[dict[str, Any]]
    total_amount: float
    status: str = "draft"  # draft, sent, paid, overdue, cancelled
    currency: str = "USD"
    created_at: float = field(default_factory=time.time)
    due_date: float = field(default_factory=lambda: time.time() + 14 * 86400)
    paid_at: Optional[float] = None
    notes: str = ""
    markdown_content: str = ""


class InvoicingEngine:
    """Manages invoices, estimates, and proposals."""

    def __init__(self) -> None:
        self.crm = get_crm()
        self._invoices: dict[str, Invoice] = {}
        self._load()

    def _load(self) -> None:
        if INVOICES_DB.exists():
            try:
                data = json.loads(INVOICES_DB.read_text(encoding="utf-8"))
                self._invoices = {k: Invoice(**v) for k, v in data.items()}
            except Exception as exc:
                log.warning("Failed to load invoices.json: %s", exc)

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._invoices.items()}
            INVOICES_DB.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save invoices.json: %s", exc)

    def create_invoice(
        self,
        client_name: str,
        items: list[dict[str, Any]],
        client_email: str = "",
        currency: str = "USD",
        due_days: int = 14,
        notes: str = "Payment due within terms. Thank you for your business.",
    ) -> Invoice:
        inv_id = f"INV-{datetime.now().strftime('%Y%m')}-{len(self._invoices)+1:03d}"
        total = sum(float(i.get("total", i.get("quantity", 1) * i.get("unit_price", 0))) for i in items)
        due_ts = time.time() + due_days * 86400

        # Generate markdown template
        items_table = "\n".join(
            f"| {i.get('description', '')} | {i.get('quantity', 1)} | ${float(i.get('unit_price', 0)):.2f} | ${float(i.get('total', 0)):.2f} |"
            for i in items
        )
        created_str = datetime.now().strftime("%Y-%m-%d")
        due_str = datetime.fromtimestamp(due_ts).strftime("%Y-%m-%d")

        md = f"""# INVOICE: {inv_id}

**Billed To:** {client_name} ({client_email or 'Client'})  
**Date:** {created_str}  
**Due Date:** {due_str}  
**Status:** DRAFT  

---

| Description | Qty | Unit Price | Total |
|---|---|---|---|
{items_table}

**Total Due: ${total:.2f} {currency}**

### Notes & Payment Instructions
{notes}
"""

        invoice = Invoice(
            id=inv_id,
            client_name=client_name,
            client_email=client_email,
            items=items,
            total_amount=round(total, 2),
            currency=currency,
            due_date=due_ts,
            notes=notes,
            markdown_content=md,
        )

        self._invoices[inv_id] = invoice
        self._save()

        # Record interaction in CRM
        self.crm.record_interaction(client_name, "invoice", f"Created invoice {inv_id} for ${total:.2f}", {"invoice_id": inv_id, "amount": total})
        return invoice

    def mark_paid(self, invoice_id: str, payment_source: str = "direct") -> bool:
        if invoice_id not in self._invoices:
            return False
        inv = self._invoices[invoice_id]
        inv.status = "paid"
        inv.paid_at = time.time()
        self._save()

        # Update CRM and revenue tracker
        self.crm.record_payment(inv.client_name, inv.total_amount, source=payment_source)
        try:
            from .revenue import get_revenue_tracker
            get_revenue_tracker().record_action(
                source=payment_source,
                action_type="invoice_paid",
                client=inv.client_name,
                amount=inv.total_amount,
                status="paid",
                meta={"invoice_id": invoice_id},
            )
        except Exception:
            pass
        return True

    def create_proposal(
        self,
        client_name: str,
        project_title: str,
        scope_summary: str,
        deliverables: list[str],
        total_price: float,
        timeline_weeks: int = 2,
    ) -> str:
        """Generate a client-ready project proposal."""
        deliv_list = "\n".join(f"- **{d}**" for d in deliverables)
        md = f"""# Project Proposal: {project_title}

**Prepared for:** {client_name}  
**Date:** {datetime.now().strftime("%Y-%m-%d")}  
**Estimated Delivery:** {timeline_weeks} weeks  
**Investment:** ${total_price:,.2f} USD  

---

## 1. Executive Summary & Objective
{scope_summary}

## 2. Key Deliverables & Milestones
{deliv_list}

## 3. Timeline & Execution Plan
- **Phase 1: Architecture & Foundations** (Week 1)
- **Phase 2: Core Development & Testing** (Week 1-2)
- **Phase 3: Final Delivery & Handover** (Week {timeline_weeks})

## 4. Terms & Next Steps
- 50% deposit to initiate sprint, 50% upon milestone delivery.
- Full IP rights transferred upon final payment.

*Ready to proceed? Confirm to trigger project onboarding.*
"""
        self.crm.add_or_update_client(name=client_name, stage="pitched", budget=total_price, notes=f"Pitched {project_title}")
        self.crm.record_interaction(client_name, "proposal", f"Generated proposal for {project_title} (${total_price:,.2f})")
        return md

    def list_invoices(self, status: Optional[str] = None) -> list[Invoice]:
        out = list(self._invoices.values())
        if status:
            out = [i for i in out if i.status == status]
        return sorted(out, key=lambda x: x.created_at, reverse=True)


_global_invoicing: Optional[InvoicingEngine] = None


def get_invoicing_engine() -> InvoicingEngine:
    global _global_invoicing
    if _global_invoicing is None:
        _global_invoicing = InvoicingEngine()
    return _global_invoicing
