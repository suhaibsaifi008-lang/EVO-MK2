import pytest
from mk2.invoicing import InvoicingEngine, Invoice, LineItem, get_invoicing_engine


@pytest.fixture
def invoicing_instance(tmp_path, monkeypatch):
    from mk2 import invoicing
    monkeypatch.setattr(invoicing, "INVOICE_DIR", tmp_path / "invoices")
    monkeypatch.setattr(invoicing, "INVOICES_DB", tmp_path / "invoices" / "invoices.json")
    return InvoicingEngine()


def test_create_invoice(invoicing_instance):
    items = [{"description": "Full-stack Agent Development", "quantity": 1, "unit_price": 2500.0}]
    inv = invoicing_instance.create_invoice("Stark Industries", items, client_email="tony@stark.com")
    assert inv.id.startswith("INV-")
    assert inv.client_name == "Stark Industries"
    assert inv.total_amount == 2500.0
    assert inv.status == "draft"


def test_mark_paid(invoicing_instance):
    items = [{"description": "API Integration", "quantity": 2, "unit_price": 500.0}]
    inv = invoicing_instance.create_invoice("Wayne Enterprises", items)
    assert invoicing_instance.mark_paid(inv.id, payment_source="stripe")
    updated = next(i for i in invoicing_instance.list_invoices() if i.id == inv.id)
    assert updated.status == "paid"
    assert updated.paid_at is not None


def test_list_pending(invoicing_instance):
    invoicing_instance.create_invoice("Client A", [{"description": "Task 1", "quantity": 1, "unit_price": 100.0}])
    inv_b = invoicing_instance.create_invoice("Client B", [{"description": "Task 2", "quantity": 1, "unit_price": 200.0}])
    invoicing_instance.mark_paid(inv_b.id)
    pending = [i for i in invoicing_instance.list_invoices() if i.status in ("draft", "sent")]
    assert len(pending) == 1
    assert pending[0].client_name == "Client A"


def test_invoice_total_calculation(invoicing_instance):
    items = [
        {"description": "Backend API", "quantity": 10, "unit_price": 150.0},
        {"description": "Frontend UI", "quantity": 5, "unit_price": 120.0},
    ]
    inv = invoicing_instance.create_invoice("Cyberdyne", items)
    assert inv.total_amount == 2100.0


def test_crm_sync(invoicing_instance):
    items = [{"description": "ML Consulting", "quantity": 1, "unit_price": 3000.0}]
    inv = invoicing_instance.create_invoice("Massive Dynamic", items)
    invoicing_instance.mark_paid(inv.id, payment_source="upwork")
    client = invoicing_instance.crm.get_client("Massive Dynamic")
    assert client is not None
    assert client.total_revenue >= 3000.0


def test_create_proposal(invoicing_instance):
    prop = invoicing_instance.create_proposal(
        client_name="Oscorp",
        project_title="Neural Search Pipeline",
        scope_summary="Semantic vector search system",
        deliverables=["Data Ingestion", "Embeddings Pipeline", "API Endpoint"],
        total_price=4500.0,
        timeline_weeks=3,
    )
    assert "Neural Search Pipeline" in prop
    assert "$4,500.00" in prop
    assert "Oscorp" in prop


def test_invoice_status_filter(invoicing_instance):
    invoicing_instance.create_invoice("Client 1", [{"description": "Item", "quantity": 1, "unit_price": 100.0}])
    inv2 = invoicing_instance.create_invoice("Client 2", [{"description": "Item", "quantity": 1, "unit_price": 200.0}])
    invoicing_instance.mark_paid(inv2.id)
    paid = invoicing_instance.list_invoices(status="paid")
    assert len(paid) == 1
    assert paid[0].client_name == "Client 2"


def test_invoice_markdown_format(invoicing_instance):
    inv = invoicing_instance.create_invoice("Hooli", [{"description": "Compression Algorithm", "quantity": 1, "unit_price": 999.0}])
    assert "# INVOICE: INV-" in inv.markdown_content
    assert "Compression Algorithm" in inv.markdown_content
    assert "$999.00" in inv.markdown_content


def test_line_item_dataclass():
    item = LineItem(description="Consulting", quantity=4.0, unit_price=250.0)
    assert item.total == 1000.0


def test_mark_paid_nonexistent(invoicing_instance):
    assert not invoicing_instance.mark_paid("INV-99999-999")
