import pytest
from mk2 import bus
from mk2.payments import PaymentDetector, PaymentEvent, get_payment_detector


@pytest.fixture
def payment_detector_instance(tmp_path, monkeypatch):
    from mk2 import crm
    monkeypatch.setattr(crm, "CRM_DIR", tmp_path / "crm")
    monkeypatch.setattr(crm, "CLIENTS_FILE", tmp_path / "crm" / "clients.json")
    monkeypatch.setattr(crm, "INTERACTIONS_FILE", tmp_path / "crm" / "interactions.json")
    local_crm = crm.CRM()
    return PaymentDetector(crm=local_crm)


def test_process_payment(payment_detector_instance):
    res = payment_detector_instance.process_payment("ev_101", "stripe", 750.0, "Acme Labs")
    assert res is True
    client = payment_detector_instance.crm.get_client("Acme Labs")
    assert client is not None
    assert client.total_revenue == 750.0


def test_duplicate_rejection(payment_detector_instance):
    res1 = payment_detector_instance.process_payment("ev_dup", "upwork", 500.0, "Beta Corp")
    res2 = payment_detector_instance.process_payment("ev_dup", "upwork", 500.0, "Beta Corp")
    assert res1 is True
    assert res2 is False


def test_bus_event_emission(payment_detector_instance):
    events = []
    bus.subscribe("money.payment_received", lambda e: events.append(e))
    payment_detector_instance.process_payment("ev_bus_test", "paypal", 300.0, "Gamma LLC")
    assert len(events) == 1
    assert events[0].payload["id"] == "ev_bus_test"
    assert events[0].payload["amount"] == 300.0


def test_crm_update_on_payment(payment_detector_instance):
    payment_detector_instance.crm.add_or_update_client("Delta Inc", stage="pitched")
    payment_detector_instance.process_payment("ev_crm_sync", "manual", 1200.0, "Delta Inc")
    c = payment_detector_instance.crm.get_client("Delta Inc")
    assert c.stage == "active"
    assert c.total_revenue == 1200.0


def test_scan_email_receipts_mock(payment_detector_instance, monkeypatch):
    class MockEmailAgent:
        def check_unread(self):
            return [
                {
                    "id": "email_msg_99",
                    "from": "service@stripe.com",
                    "subject": "You received a payment of $850.00",
                    "body": "Customer Enterprise Co paid $850.00 USD.",
                }
            ]

    monkeypatch.setattr("mk2.email_agent.get_email_agent", lambda: MockEmailAgent())
    detected = payment_detector_instance.scan_email_receipts()
    assert len(detected) == 1
    assert detected[0].amount == 850.0
    assert detected[0].source == "stripe"


def test_stripe_balance_scan_no_credentials(payment_detector_instance, monkeypatch):
    class MockVault:
        def get(self, key):
            return None

    monkeypatch.setattr("mk2.credential_vault.get_credential_vault", lambda: MockVault())
    detected = payment_detector_instance.scan_stripe_balance()
    assert detected == []


def test_scan_all_aggregation(payment_detector_instance, monkeypatch):
    monkeypatch.setattr(payment_detector_instance, "scan_email_receipts", lambda: [PaymentEvent("e1", "email", 100.0, "C1")])
    monkeypatch.setattr(payment_detector_instance, "scan_stripe_balance", lambda: [PaymentEvent("s1", "stripe", 200.0, "C2")])
    all_events = payment_detector_instance.scan_all()
    assert len(all_events) == 2


def test_payment_event_dataclass():
    ev = PaymentEvent(id="p1", source="upwork", amount=450.0, client="TechStart", currency="USD")
    assert ev.id == "p1"
    assert ev.amount == 450.0
    assert ev.currency == "USD"


def test_multi_currency_handling(payment_detector_instance):
    payment_detector_instance.process_payment("ev_eur", "wire", 1000.0, "Euro Corp", meta={"currency": "EUR"})
    c = payment_detector_instance.crm.get_client("Euro Corp")
    assert c is not None
    assert c.total_revenue == 1000.0


def test_get_payment_detector_singleton():
    d1 = get_payment_detector()
    d2 = get_payment_detector()
    assert d1 is d2
