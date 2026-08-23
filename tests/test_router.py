import pytest

from mk2 import db, llm


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()


def test_provider_failover_primary_to_ollama(monkeypatch):
    monkeypatch.setattr(llm.settings, "openai_key", "k")
    monkeypatch.setattr(llm.settings, "ollama_base", "http://x/v1")
    monkeypatch.setattr(llm.settings, "ollama_model", "qwen3:4b")
    calls = []
    def fake(base, key, payload, timeout=60):
        calls.append(payload["model"])
        if base.endswith("api.openai.com/v1"):
            raise ConnectionError("down")
        return {"choices": [{"message": {"content": "OLLAMA"}}]}
    monkeypatch.setattr(llm, "_completion", fake)
    out = llm.chat([{"role": "user", "content": "hi"}])
    assert out == "OLLAMA" and len(calls) >= 2


def test_all_down_raises():
    from mk2 import config as cfg
    monkey = pytest.MonkeyPatch()
    monkey.setattr(cfg.settings, "openai_key", "")
    monkey.setattr(cfg.settings, "ollama_base", "")
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "x"}])
    monkey.undo()


def test_role_routing_fast_model():
    from mk2 import llm as L

    seen = {}
    def fake(base, key, payload, timeout=60):
        seen["model"] = payload["model"]
        return {"choices": [{"message": {"content": "ok"}}]}
    orig = L._completion
    L._completion = fake
    L._attempts_orig = None
    import mk2.config as cfgmod
    cfgmod.settings.fast_model = "nano-1"
    try:
        L.chat([{"role": "user", "content": "hi"}], role="fast")
        assert seen["model"] == "nano-1"
    finally:
        L._completion = orig
