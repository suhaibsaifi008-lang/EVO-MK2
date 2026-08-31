import time
import pytest
from mk2.crm import CRM, Client, Interaction, get_crm


@pytest.fixture
def crm_instance(tmp_path, monkeypatch):
    from mk2 import crm
    monkeypatch.setattr(crm, "CRM_DIR", tmp_path / "crm")
    monkeypatch.setattr(crm, "CLIENTS_FILE", tmp_path / "crm" / "clients.json")
    monkeypatch.setattr(crm, "INTERACTIONS_FILE", tmp_path / "crm" / "interactions.json")
    return CRM()


def test_add_client(crm_instance):
    c = crm_instance.add_or_update_client("Globex Corp", "info@globex.com", platform="upwork", stage="lead", budget=1500.0)
    assert c.name == "Globex Corp"
    assert c.email == "info@globex.com"
    assert c.budget == 1500.0
    assert c.stage == "lead"


def test_update_stage(crm_instance):
    crm_instance.add_or_update_client("Globex Corp", stage="lead")
    updated = crm_instance.add_or_update_client("Globex Corp", stage="in_discussion")
    assert updated.stage == "in_discussion"


def test_record_interaction(crm_instance):
    crm_instance.add_or_update_client("Initech", stage="pitched")
    crm_instance.record_interaction("Initech", "proposal", "Sent initial architecture proposal", {"bid": 2000})
    interactions = crm_instance.list_interactions(client_name="Initech")
    assert len(interactions) == 1
    assert interactions[0].kind == "proposal"
    assert "architecture" in interactions[0].summary


def test_lead_scoring_budget(crm_instance):
    c_low = crm_instance.add_or_update_client("Small Client", budget=50.0, stage="lead")
    c_high = crm_instance.add_or_update_client("Big Client", budget=5000.0, stage="lead")
    assert c_high.lead_score > c_low.lead_score


def test_lead_scoring_stage(crm_instance):
    c_lead = crm_instance.add_or_update_client("Client A", budget=1000.0, stage="lead")
    c_active = crm_instance.add_or_update_client("Client B", budget=1000.0, stage="active")
    assert c_active.lead_score > c_lead.lead_score


def test_get_active_leads(crm_instance):
    crm_instance.add_or_update_client("Client Lead", stage="lead")
    crm_instance.add_or_update_client("Client Pitched", stage="pitched")
    crm_instance.add_or_update_client("Client Discuss", stage="in_discussion")
    crm_instance.add_or_update_client("Client Churned", stage="churned")
    active = [c for c in crm_instance.list_clients() if c.stage in ("pitched", "in_discussion", "contract_sent")]
    assert len(active) == 2


def test_get_pipeline_summary(crm_instance):
    crm_instance.add_or_update_client("Client 1", budget=1000.0, stage="lead")
    crm_instance.add_or_update_client("Client 2", budget=3000.0, stage="pitched")
    crm_instance.add_or_update_client("Client 3", budget=2000.0, stage="active")
    summary = crm_instance.get_pipeline_summary()
    assert summary["total_clients"] == 3
    assert summary["pipeline_value"] == 6000.0
    assert summary["stages"]["pitched"] == 1


def test_search_clients(crm_instance):
    crm_instance.add_or_update_client("Umbrella Corp", notes="Python AI pipeline")
    crm_instance.add_or_update_client("Wayne Tech", notes="React frontend")
    results = [c for c in crm_instance.list_clients() if "umbrella" in c.name.lower()]
    assert len(results) == 1
    assert results[0].name == "Umbrella Corp"


def test_record_payment_crm(crm_instance):
    crm_instance.add_or_update_client("Soylent Corp", stage="contract_sent")
    crm_instance.record_payment("Soylent Corp", 2500.0, source="stripe")
    client = crm_instance.get_client("Soylent Corp")
    assert client.stage == "active"
    assert client.total_revenue == 2500.0


def test_crm_persistence(tmp_path, monkeypatch):
    from mk2 import crm
    monkeypatch.setattr(crm, "CRM_DIR", tmp_path / "crm")
    monkeypatch.setattr(crm, "CLIENTS_FILE", tmp_path / "crm" / "clients.json")
    monkeypatch.setattr(crm, "INTERACTIONS_FILE", tmp_path / "crm" / "interactions.json")

    crm1 = CRM()
    crm1.add_or_update_client("Persistent Client", budget=4000.0, stage="pitched")

    crm2 = CRM()
    c = crm2.get_client("Persistent Client")
    assert c is not None
    assert c.budget == 4000.0
    assert c.stage == "pitched"
