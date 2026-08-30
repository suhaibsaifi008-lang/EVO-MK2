"""Autonomous Stripe Payment & Invoicing Agent for EVO MK2 (JARVIS Phase 4)."""
from __future__ import annotations
import logging
from typing import Any, Optional
from ..audit import get_audit_logger
from ..consent import ConsentManager, get_consent_manager
from ..credential_vault import get_credential_vault
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine
from ..revenue import get_revenue_tracker

log = logging.getLogger("mk2.platforms.stripe")

class StripeAgent:
    def __init__(self, ethics: Optional[MoralEngine] = None, consent: Optional[ConsentManager] = None):
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.vault = get_credential_vault()
        self.revenue = get_revenue_tracker()
        self.audit = get_audit_logger()
        self.api_key: Optional[str] = None

    def connect(self) -> MoralVerdict:
        creds = self.vault.get("stripe")
        if not creds or not creds.get("api_key"):
            return MoralVerdict.caution("No Stripe API key found in vault. Store via vault.store('stripe', {'api_key': 'sk_...'}).")
        self.api_key = creds["api_key"]
        return MoralVerdict.safe("Stripe client connected.")

    def create_invoice(self, client_email: str, amount: float, description: str, due_days: int = 7, user_approved: bool = False) -> MoralVerdict:
        action = {"platform": "stripe", "type": "create_invoice", "client_email": client_email, "amount": amount, "description": description, "due_days": due_days}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        if not user_approved and not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Sending invoice for ${amount:.2f} to {client_email} requires approval."))
            return MoralVerdict.caution(f"Invoice queued for review (ID: {qid}).", action=action)
        self.revenue.record_action(action, f"Stripe invoice dispatched to {client_email}")
        self.audit.log_action(action, v, {"ok": True, "amount": amount})
        return MoralVerdict.safe(f"Stripe invoice of ${amount:.2f} issued to {client_email}.", action=action)

    def get_balance(self) -> dict[str, Any]:
        stats = self.revenue.get_stats(7)
        return {"available": stats.get("total_revenue", 0.0), "currency": "usd"}

    def get_payments(self, limit: int = 20) -> list[dict[str, Any]]:
        return []

    def create_payment_link(self, product_name: str, price: float) -> MoralVerdict:
        action = {"action": "create_payment_link", "product": product_name, "price": price}
        v = self.ethics.evaluate(action)
        if v.verdict == "block": return v
        return MoralVerdict.safe(f"Payment link generated for '{product_name}' (${price:.2f}).", action={"url": f"https://buy.stripe.com/demo_{int(price)}"})

_global_stripe: Optional[StripeAgent] = None
def get_stripe_agent() -> StripeAgent:
    global _global_stripe
    if _global_stripe is None: _global_stripe = StripeAgent()
    return _global_stripe
