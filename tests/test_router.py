import pytest

from mk2 import db, llm


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()


def test_provider_failover_primary_to_ollama(monkeypatch):
    provs = [
        {"name": "primary", "base": "https://primary.test/v1", "key": "k",
         "default_model": "gpt-x", "timeout_bias": 0},
        {"name": "ollama", "base": "http://ollama.test/v1", "key": "",
         "default_model": "qwen3:4b", "timeout_bias": 0},
    ]
    monkeypatch.setattr(llm, "_providers", lambda: provs)
    calls = []
    def fake(base, key, payload, timeout=60):
        calls.append((base, payload["model"]))
        if base.startswith("https://primary"):
            raise ConnectionError("down")
        return {"choices": [{"message": {"content": "OLLAMA"}}]}
    monkeypatch.setattr(llm, "_completion", fake)
    out = llm.chat([{"role": "user", "content": "hi"}])
    assert out == "OLLAMA"
    assert len(calls) >= 2 and calls[0][0].startswith("https://primary")


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
