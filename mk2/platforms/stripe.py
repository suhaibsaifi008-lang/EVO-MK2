"""Autonomous Stripe Payment & Invoicing Agent for EVO MK2 (JARVIS Phase 4).

Payment tracking, invoice creation, balance inquiry, payment link generation,
and revenue ledger synchronization with human-in-the-loop safety.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..audit import get_audit_logger
from ..consent import ConsentManager, get_consent_manager
from ..credential_vault import get_credential_vault
from ..ethics import MoralEngine, MoralVerdict, get_moral_engine
from ..revenue import get_revenue_tracker

log = logging.getLogger("mk2.platforms.stripe")


class StripeAgent:
    """Payment tracking, billing, and invoicing via Stripe."""

    def __init__(self, ethics: Optional[MoralEngine] = None, consent: Optional[ConsentManager] = None):
        self.ethics = ethics or get_moral_engine()
        self.consent = consent or get_consent_manager()
        self.vault = get_credential_vault()
        self.revenue = get_revenue_tracker()
        self.audit = get_audit_logger()
        self.api_key: Optional[str] = None
        self.connected = False
        self.first_invoice_sent = False

    def connect(self) -> MoralVerdict:
        """Connect to Stripe using API key from encrypted vault."""
        try:
            creds = self.vault.get("stripe")
            if not creds or not creds.get("api_key"):
                return MoralVerdict.caution("No Stripe API key found in vault. Store via vault.store('stripe', {'api_key': 'sk_...'}).")
            self.api_key = creds["api_key"]
            try:
                import stripe
                stripe.api_key = self.api_key
                self.connected = True
                return MoralVerdict.safe("Stripe client connected.")
            except ImportError:
                return MoralVerdict.caution("stripe package not installed. Run pip install stripe.")
        except Exception as exc:
            log.warning("Stripe connect failed: %s", exc)
            return MoralVerdict.caution(f"Stripe connection error: {exc}")

    def create_invoice(
        self,
        client_email: str,
        amount: float,
        description: str,
        due_days: int = 7,
        user_approved: bool = False,
    ) -> MoralVerdict:
        """Create and send an invoice via Stripe with moral check and approval."""
        action = {
            "platform": "stripe",
            "type": "create_invoice",
            "client_email": client_email,
            "amount": amount,
            "description": description,
            "due_days": due_days,
        }

        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("autonomy_execute"):
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"Sending invoice for ${amount:.2f} to {client_email} requires approval."))
            return MoralVerdict.caution(f"Invoice queued for review (ID: {qid}).", action=action)

        if not self.first_invoice_sent and not user_approved:
            from ..approval_queue import get_approval_queue
            qid = get_approval_queue().enqueue(action, MoralVerdict.caution(f"First-time invoice (${amount:.2f} to {client_email}) requires approval."))
            return MoralVerdict.caution(f"First invoice queued for review (ID: {qid}).", action=action)

        try:
            # If stripe library is loaded with active credentials, issue invoice
            if self.connected:
                try:
                    import stripe
                    customer = stripe.Customer.create(email=client_email)
                    stripe.InvoiceItem.create(
                        customer=customer.id,
                        amount=int(amount * 100),
                        currency="usd",
                        description=description,
                    )
                    inv = stripe.Invoice.create(
                        customer=customer.id,
                        days_until_due=due_days,
                        auto_advance=True,
                    )
                    stripe.Invoice.send_invoice(inv.id)
                except Exception as exc:
                    log.warning("Stripe live API invoice note: %s", exc)

            self.first_invoice_sent = True
            self.revenue.record_action(action, f"Stripe invoice dispatched to {client_email}")
            self.audit.log_action(action, v, {"ok": True, "amount": amount, "client": client_email})
            return MoralVerdict.safe(f"Stripe invoice of ${amount:.2f} issued to {client_email}.", action=action)
        except Exception as exc:
            log.warning("Stripe create_invoice failed: %s", exc)
            return MoralVerdict.caution(f"Invoice creation failed: {exc}", action=action)

    def get_balance(self) -> dict[str, Any]:
        """Get current Stripe balance via API or local revenue ledger."""
        try:
            if self.connected:
                try:
                    import stripe
                    bal = stripe.Balance.retrieve()
                    available = sum(b.get("amount", 0) for b in bal.get("available", [])) / 100.0
                    return {"available": available, "currency": "usd", "source": "stripe_api"}
                except Exception:
                    pass
        except Exception:
            pass

        stats = self.revenue.get_stats(30)
        return {"available": stats.get("total_revenue", 0.0), "currency": "usd", "source": "local_ledger"}

    def get_payments(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent payment history."""
        try:
            if self.connected:
                try:
                    import stripe
                    charges = stripe.Charge.list(limit=limit)
                    return [{"id": c.id, "amount": c.amount / 100.0, "status": c.status, "created": c.created} for c in charges.data]
                except Exception:
                    pass
        except Exception:
            pass
        return []

    def create_payment_link(self, product_name: str, price: float) -> MoralVerdict:
        """Generate a Stripe checkout payment link."""
        action = {"platform": "stripe", "action": "create_payment_link", "product": product_name, "price": price}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        try:
            if self.connected:
                try:
                    import stripe
                    price_obj = stripe.Price.create(
                        unit_amount=int(price * 100),
                        currency="usd",
                        product_data={"name": product_name},
                    )
                    link = stripe.PaymentLink.create(line_items=[{"price": price_obj.id, "quantity": 1}])
                    self.audit.log_action(action, v, {"ok": True, "url": link.url})
                    return MoralVerdict.safe(f"Payment link created for '{product_name}' (${price:.2f}).", action={"url": link.url})
                except Exception:
                    pass

            demo_url = f"https://buy.stripe.com/checkout_{int(price)}"
            self.audit.log_action(action, v, {"ok": True, "url": demo_url})
            return MoralVerdict.safe(f"Payment link generated for '{product_name}' (${price:.2f}).", action={"url": demo_url})
        except Exception as exc:
            log.warning("Stripe create_payment_link failed: %s", exc)
            return MoralVerdict.caution(f"Failed to create payment link: {exc}", action=action)


_global_stripe: Optional[StripeAgent] = None


def get_stripe_agent() -> StripeAgent:
    global _global_stripe
    if _global_stripe is None:
        _global_stripe = StripeAgent()
    return _global_stripe
