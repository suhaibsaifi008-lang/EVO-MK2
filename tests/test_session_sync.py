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
