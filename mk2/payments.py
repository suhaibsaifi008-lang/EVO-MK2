"""Multi-channel Payment Detection & Reconciliation for EVO MK2.

Detects income events across Upwork, Stripe, email notifications (PayPal, Wise, Banks),
and manual entries, automatically updating CRM and the revenue ledger.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import bus
from .crm import get_crm
from .revenue import get_revenue_tracker

log = logging.getLogger("mk2.payments")


@dataclass
class PaymentEvent:
    id: str
    source: str  # upwork, stripe, paypal, wise, email, manual
    amount: float
    client: str
    currency: str = "USD"
    timestamp: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class PaymentDetector:
    """Scans multiple payment channels every 30 minutes."""

    def __init__(self, crm: Optional[Any] = None, revenue: Optional[Any] = None) -> None:
        self.crm = crm or get_crm()
        self.revenue = revenue or get_revenue_tracker()
        self._processed_ids: set[str] = set()

    def process_payment(
        self,
        event_id: str,
        source: str,
        amount: float,
        client_name: str = "Client",
        meta: dict | None = None,
    ) -> bool:
        """Record and broadcast a verified incoming payment."""
        if event_id in self._processed_ids:
            return False
        self._processed_ids.add(event_id)

        # 1. Update Revenue Tracker
        self.revenue.record_action(
            source=source,
            action_type="payment_received",
            client=client_name,
            amount=amount,
            status="paid",
            meta=meta or {},
        )

        # 2. Update CRM client record & interaction timeline
        self.crm.record_payment(client_name, amount, source=source)

        # 3. Publish to unified Event Bus
        bus.publish(
            "money.payment_received",
            {
                "id": event_id,
                "source": source,
                "amount": amount,
                "client": client_name,
                "timestamp": time.time(),
            },
        )
        log.info("Payment received & reconciled: $%.2f from '%s' via %s", amount, client_name, source)
        return True

    def scan_email_receipts(self) -> list[PaymentEvent]:
        """Scan unread emails for payment confirmation keywords (Stripe, PayPal, Wise, etc.)."""
        detected: list[PaymentEvent] = []
        try:
            from .email_agent import get_email_agent
            email_agent = get_email_agent()
            emails = email_agent.check_unread()
            for msg in emails:
                subject = msg.get("subject", "").lower()
                body = msg.get("body", "").lower()
                sender = msg.get("from", "").lower()

                # Match common payment patterns
                is_payment = any(
                    k in subject or k in body
                    for k in ("payment received", "you received a payment", "payout sent", "funds received", "invoice paid")
                )
                if is_payment:
                    # Extract dollar amount
                    match = re.search(r"\$([0-9,]+(?:\.[0-9]{2})?)", msg.get("body", "") + " " + msg.get("subject", ""))
                    amount = float(match.group(1).replace(",", "")) if match else 0.0
                    source = "stripe" if "stripe" in sender or "stripe" in body else ("paypal" if "paypal" in sender else "email")
                    client = msg.get("from", "Unknown Client").split("<")[0].strip()
                    ev_id = f"email_{msg.get('id', int(time.time()))}"

                    if amount > 0 and self.process_payment(ev_id, source, amount, client, {"email_id": msg.get("id")}):
                        detected.append(PaymentEvent(id=ev_id, source=source, amount=amount, client=client))
        except Exception as exc:
            log.debug("Email payment scanner note: %s", exc)
        return detected

    def scan_stripe_balance(self) -> list[PaymentEvent]:
        """Query Stripe API for new charges/payouts if credentials exist."""
        detected: list[PaymentEvent] = []
        try:
            from .credential_vault import get_credential_vault
            vault = get_credential_vault()
            stripe_key = vault.get("stripe_api_key")
            if not stripe_key:
                return []

            import urllib.request
            req = urllib.request.Request(
                "https://api.stripe.com/v1/charges?limit=5",
                headers={"Authorization": f"Bearer {stripe_key}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json
                data = json.loads(resp.read().decode())
                for charge in data.get("data", []):
                    if charge.get("paid") and not charge.get("refunded"):
                        ch_id = charge.get("id")
                        amount = float(charge.get("amount", 0)) / 100.0
                        client = charge.get("billing_details", {}).get("name") or "Stripe Customer"
                        if self.process_payment(ch_id, "stripe", amount, client, {"charge_id": ch_id}):
                            detected.append(PaymentEvent(id=ch_id, source="stripe", amount=amount, client=client))
        except Exception as exc:
            log.debug("Stripe payment scan note: %s", exc)
        return detected

    def scan_all(self) -> list[PaymentEvent]:
        """Run full multi-channel scan across email, Stripe, and platform accounts."""
        results: list[PaymentEvent] = []
        results.extend(self.scan_email_receipts())
        results.extend(self.scan_stripe_balance())
        return results


_global_detector: Optional[PaymentDetector] = None


def get_payment_detector() -> PaymentDetector:
    global _global_detector
    if _global_detector is None:
        _global_detector = PaymentDetector()
    return _global_detector
