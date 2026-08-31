import pytest
from mk2 import bus
from mk2.money_briefing import MoneyBriefingEngine, get_money_briefing_engine


@pytest.fixture
def briefing_instance(tmp_path, monkeypatch):
    from mk2 import crm, invoicing
    monkeypatch.setattr(crm, "CRM_DIR", tmp_path / "crm")
    monkeypatch.setattr(crm, "CLIENTS_FILE", tmp_path / "crm" / "clients.json")
    monkeypatch.setattr(crm, "INTERACTIONS_FILE", tmp_path / "crm" / "interactions.json")
    monkeypatch.setattr(invoicing, "INVOICE_DIR", tmp_path / "invoices")
    monkeypatch.setattr(invoicing, "INVOICES_DB", tmp_path / "invoices" / "invoices.json")
    local_crm = crm.CRM()
    local_inv = invoicing.InvoicingEngine()
    local_inv.crm = local_crm
    return MoneyBriefingEngine(crm=local_crm, invoicing=local_inv)


def test_generate_briefing_structure(briefing_instance):
    res = briefing_instance.generate_briefing()
    assert res["ok"] is True
    assert "markdown" in res
    assert "total_revenue" in res
    assert "pipeline_value" in res
    assert "top_actions" in res


def test_top_actions_populated(briefing_instance):
    briefing_instance.crm.add_or_update_client("High Value Lead", budget=6000.0, stage="in_discussion")
    res = briefing_instance.generate_briefing()
    actions = res["top_actions"]
    assert len(actions) >= 1
    assert any("High Value Lead" in a for a in actions)


def test_revenue_calculation(briefing_instance):
    res = briefing_instance.generate_briefing()
    assert isinstance(res["total_revenue"], float)
    assert res["total_revenue"] >= 0.0


def test_briefing_caching(briefing_instance):
    res1 = briefing_instance.generate_briefing()
    res2 = briefing_instance.generate_briefing()
    assert res1["ok"] and res2["ok"]


def test_vault_note_export(briefing_instance, monkeypatch, tmp_path):
    monkeypatch.setattr("mk2.vault.write_note", lambda slug, content, tags: tmp_path / f"{slug}.md")
    res = briefing_instance.generate_briefing()
    assert res["ok"] is True
    assert res["vault_path"] != ""


def test_bus_event_emission(briefing_instance):
    events = []
    bus.subscribe("money.briefing_generated", lambda e: events.append(e))
    briefing_instance.generate_briefing()
    assert len(events) >= 1
    assert "total_revenue" in events[0].payload
    assert "top_actions" in events[0].payload


def test_hot_leads_inclusion_in_actions(briefing_instance):
    briefing_instance.crm.add_or_update_client("Hot Prospect", budget=8500.0, stage="pitched")
    res = briefing_instance.generate_briefing()
    assert any("Hot Prospect" in a for a in res["top_actions"])


def test_pending_invoices_in_briefing(briefing_instance):
    briefing_instance.invoicing.create_invoice("Billing Client", [{"description": "Dev", "quantity": 1, "unit_price": 2000.0}])
    res = briefing_instance.generate_briefing()
    md = res["markdown"]
    assert "INV-" in md
    assert "Billing Client" in md


def test_empty_pipeline_briefing(briefing_instance):
    res = briefing_instance.generate_briefing()
    assert res["pipeline_value"] == 0.0
    assert "No pending invoices." in res["markdown"]


def test_markdown_formatting_sections(briefing_instance):
    res = briefing_instance.generate_briefing()
    md = res["markdown"]
    assert "# Daily Money & Revenue Briefing" in md
    assert "Executive Financial Snapshot" in md
    assert "Active Pipeline Breakdown" in md
    assert "Top 3 Recommended Revenue Actions Today" in md
