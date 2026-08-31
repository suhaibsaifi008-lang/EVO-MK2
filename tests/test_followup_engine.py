import time
import pytest
from mk2.crm import Client
from mk2.followup_engine import FollowupEngine, FollowUpAction, get_followup_engine


@pytest.fixture
def followup_instance(tmp_path, monkeypatch):
    from mk2 import crm
    monkeypatch.setattr(crm, "CRM_DIR", tmp_path / "crm")
    monkeypatch.setattr(crm, "CLIENTS_FILE", tmp_path / "crm" / "clients.json")
    monkeypatch.setattr(crm, "INTERACTIONS_FILE", tmp_path / "crm" / "interactions.json")
    local_crm = crm.CRM()
    return FollowupEngine(crm=local_crm)


def test_detect_dormant_lead(followup_instance):
    # Add a pitched client updated 4 days ago
    client = followup_instance.crm.add_or_update_client("Dormant Corp", stage="pitched")
    client.updated_at = time.time() - (4 * 86400)
    followup_instance.crm._save()

    actions = followup_instance.get_pending_followups()
    assert len(actions) >= 1
    assert any(a.client_name == "Dormant Corp" for a in actions)


def test_cadence_timing_24h_72h_7d(followup_instance):
    c1 = Client(id="1", name="Client 1", email="", platform="upwork", stage="in_discussion", budget=500.0, updated_at=time.time() - 1.5 * 86400)
    c2 = Client(id="2", name="Client 2", email="", platform="upwork", stage="pitched", budget=1500.0, updated_at=time.time() - 4 * 86400)
    c3 = Client(id="3", name="Client 3", email="", platform="upwork", stage="pitched", budget=2500.0, updated_at=time.time() - 8 * 86400)

    draft1 = followup_instance.generate_followup_draft(c1, "24h")
    draft2 = followup_instance.generate_followup_draft(c2, "72h")
    draft3 = followup_instance.generate_followup_draft(c3, "7d")

    assert "yesterday" in draft1.lower() or "following up" in draft1.lower()
    assert "proposal" in draft2.lower()
    assert "revisit" in draft3.lower() or "circling back" in draft3.lower()


def test_generate_draft(followup_instance):
    c = Client(id="c_test", name="Alice Smith", email="alice@test.com", platform="email", stage="pitched", budget=1200.0)
    draft = followup_instance.generate_followup_draft(c, "72h")
    assert "Alice" in draft
    assert len(draft) > 40


def test_record_followup_sent(followup_instance):
    followup_instance.crm.add_or_update_client("Echo Corp", stage="pitched")
    followup_instance.record_followup_sent("Echo Corp", "Checking in on proposal terms")
    interactions = followup_instance.crm.list_interactions("Echo Corp")
    assert len(interactions) == 1
    assert interactions[0].kind == "followup"


def test_active_lead_exclusion(followup_instance):
    # Active paying or completed clients do not need pitch follow-ups
    c_active = followup_instance.crm.add_or_update_client("Active Client", stage="active")
    c_active.updated_at = time.time() - (10 * 86400)
    followup_instance.crm._save()

    actions = followup_instance.get_pending_followups()
    assert not any(a.client_name == "Active Client" for a in actions)


def test_custom_lead_name_personalization(followup_instance):
    c = Client(id="c_pers", name="Marcus Aurelius", email="", platform="upwork", stage="pitched", budget=5000.0)
    draft = followup_instance.generate_followup_draft(c, "24h")
    assert "Marcus" in draft


def test_pending_followups_sorting(followup_instance):
    c_older = followup_instance.crm.add_or_update_client("Older Deal", stage="pitched")
    c_older.updated_at = time.time() - (10 * 86400)
    c_newer = followup_instance.crm.add_or_update_client("Newer Deal", stage="pitched")
    c_newer.updated_at = time.time() - (3.5 * 86400)
    followup_instance.crm._save()

    actions = followup_instance.get_pending_followups()
    assert len(actions) >= 2
    assert actions[0].days_since_update >= actions[1].days_since_update


def test_empty_crm_followups(followup_instance):
    actions = followup_instance.get_pending_followups()
    assert actions == []


def test_contract_sent_cadence(followup_instance):
    c_contract = followup_instance.crm.add_or_update_client("Contract Client", stage="contract_sent")
    c_contract.updated_at = time.time() - (7.5 * 86400)
    followup_instance.crm._save()

    actions = followup_instance.get_pending_followups()
    assert any(a.client_name == "Contract Client" and a.cadence == "7d" for a in actions)


def test_get_followup_engine_singleton():
    f1 = get_followup_engine()
    f2 = get_followup_engine()
    assert f1 is f2
