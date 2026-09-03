import pytest
from fastapi.testclient import TestClient
from mk2.server import app


client = TestClient(app)


def test_session_state_endpoint():
    res = client.get("/api/session/state")
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") is True
    assert "recent_turns" in data
    assert "facts" in data


def test_pairing_workflow():
    # 1. Request pairing
    res = client.post("/api/pair/request", json={"device_name": "Suhaib's Laptop"})
    assert res.status_code == 200
    data = res.json()
    code = data.get("code")
    assert code and len(code) == 6

    # 2. Approve pairing
    approve_res = client.post("/api/pair/approve", json={"code": code})
    assert approve_res.status_code == 200
    assert approve_res.json().get("ok") is True

    # 3. Sync status endpoint with approved pairing code
    status_res = client.get("/api/sync/status", headers={"x-pair-code": code})
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data.get("ok") is True
    assert "device_id" in status_data
    assert "device_name" in status_data

    # 4. Sync pull with pairing code
    pull_res = client.get("/api/sync/pull", headers={"x-pair-code": code})
    assert pull_res.status_code == 200
    bundle = pull_res.json()
    assert bundle.get("version") == "mk2-sync-1.0"
    assert "signature" in bundle

    # 5. Sync push with valid signature
    push_res = client.post("/api/sync/push", json=bundle, headers={"x-pair-code": code})
    assert push_res.status_code == 200
    assert push_res.json().get("ok") is True

    # 6. Tampered signature rejected
    tampered = dict(bundle)
    tampered["signature"] = "bad" + tampered["signature"][3:]
    tampered_res = client.post("/api/sync/push", json=tampered, headers={"x-pair-code": code})
    assert tampered_res.status_code == 400
