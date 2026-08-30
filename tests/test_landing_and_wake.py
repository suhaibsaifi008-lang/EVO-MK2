import pytest
from fastapi.testclient import TestClient
from mk2.server import app
from mk2.voice import wake


client = TestClient(app)


def test_landing_endpoint():
    res = client.get("/landing")
    assert res.status_code == 200
    assert "EVO MK2" in res.text
    assert "WAKE WORD:" in res.text
    assert '"EVO"' in res.text
    assert "railCarriage" in res.text


def test_wake_word_evo_matching():
    # Exact single-word "evo"
    assert wake.match_wake("evo") == ""
    assert wake.match_wake("EVO") == ""

    # "hey evo" & "ok evo"
    assert wake.match_wake("hey evo") == ""
    assert wake.match_wake("ok evo") == ""

    # "evo" with command trailing
    assert wake.match_wake("evo what is the weather today") == "what is the weather today"
    assert wake.match_wake("hey evo open chrome") == "open chrome"
    assert wake.match_wake("wake up evo run tests") == "run tests"

    # Non-wake phrases should not match
    assert wake.match_wake("hello world") is None
    assert wake.match_wake("what is the weather") is None
