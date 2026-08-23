import pytest

from mk2 import db, llm


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()


def test_provider_failover_primary_to_ollama(monkeypatch):
    provs = [
        {"name": "primary", "kind": "openai", "base": "https://primary.test/v1",
         "key": "k", "default_model": "gpt-x", "timeout_bias": 0},
        {"name": "ollama", "kind": "openai", "base": "http://ollama.test/v1",
         "key": "", "default_model": "qwen3:4b", "timeout_bias": 0},
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


def test_all_providers_down_raises(monkeypatch):
    provs = [
        {"name": "a", "kind": "openai", "base": "https://a.test/v1", "key": "",
         "default_model": "m1", "timeout_bias": 0},
        {"name": "b", "kind": "openai", "base": "https://b.test/v1", "key": "",
         "default_model": "m2", "timeout_bias": 0},
    ]
    monkeypatch.setattr(llm, "_providers", lambda: provs)

    def dead(base, key, payload, timeout=60):
        raise ConnectionError("down")

    monkeypatch.setattr(llm, "_completion", dead)
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "x"}])


def test_role_routing_uses_provider_default_model(monkeypatch):
    """With no fast_model override, each provider serves its own default."""
    provs = [
        {"name": "gemini", "kind": "gemini", "base": "", "key": "k",
         "default_model": "gemini-3.5-flash-lite", "timeout_bias": 0},
    ]
    monkeypatch.setattr(llm, "_providers", lambda: provs)
    seen = {}

    def fake(client, messages, model, temperature):
        seen["model"] = model
        return "ok"

    class FakeClient:
        pass

    monkeypatch.setattr(llm, "_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(llm, "_gemini_chat", fake)
    llm.chat([{"role": "user", "content": "hi"}], role="fast")
    assert seen["model"] == "gemini-3.5-flash-lite"


def test_stream_falls_back_to_gemini_oneshot(monkeypatch):
    from mk2 import llm as L

    provs = [{"name": "gemini", "kind": "gemini", "base": "", "key": "k",
              "default_model": "gm", "timeout_bias": 0}]
    monkeypatch.setattr(L, "_providers", lambda: provs)

    class FakeModels:
        def generate_content_stream(self, model, contents, config):
            class C:
                text = "HELLO STREAM"
            yield C()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(L, "_gemini_client", lambda: FakeClient())
    out = "".join(L.chat_stream([{"role": "user", "content": "hi"}]))
    assert out == "HELLO STREAM"
