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


def _freellm_provs():
    return [
        {"name": "freellmapi", "kind": "openai", "base": "http://f.test/v1",
         "key": "k", "default_model": "ignored", "timeout_bias": 0},
        {"name": "gemini", "kind": "gemini", "base": "", "key": "k",
         "default_model": "gm", "timeout_bias": 0},
    ]


class TestTokenCascade:
    def test_ladder_expands_top_to_bottom_then_other_providers(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)
        att = llm._attempts("primary")
        models = [m for p, m in att]
        assert models[0] == llm.PRIMARY_LADDER[0]          # best model first
        assert models.index("qwen3.6-27b") > models.index("gpt-oss-120b")
        assert ("gemini", "gm") in [(p["name"], m) for p, m in att]  # fallback tail

    def test_quota_error_cascades_and_cools_down(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)

        def fake(base, key, payload, timeout=60):
            if payload["model"] == llm.PRIMARY_LADDER[0]:
                raise ConnectionError("HTTP Error 429: quota exceeded")
            return {"choices": [{"message": {"content": "NEXT MODEL"}}]}
        monkeypatch.setattr(llm, "_completion", fake)
        out = llm.chat([{"role": "user", "content": "hi"}])
        assert out == "NEXT MODEL"
        # exhausted model is now in cooldown -> skipped on next call
        att = [m for p, m in llm._attempts("primary")]
        assert llm.PRIMARY_LADDER[0] not in att
        assert att[0] == llm.PRIMARY_LADDER[1]

    def test_soft_errors_cool_briefly_not_forever(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)
        with llm._cd_lock:
            llm._cooldowns.clear()
        llm._penalize("freellmapi", "gpt-oss-120b", "ConnectionResetError")
        att = [m for p, m in llm._attempts("primary")]
        assert "gpt-oss-120b" not in att          # cooling (60s)
        import time as _t
        with llm._cd_lock:
            llm._cooldowns["freellmapi:gpt-oss-120b"] = _t.time() - 1
        att = [m for p, m in llm._attempts("primary")]
        assert att[0] == "gpt-oss-120b"           # back on top after expiry

    def test_explicit_override_pins_model(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)
        att = llm._attempts("primary", model_override="inkling")
        fm = [(p["name"], m) for p, m in att if p["name"] == "freellmapi"]
        assert fm == [("freellmapi", "inkling")]

    def test_all_cooling_falls_through_then_retries(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)
        with llm._cd_lock:
            for m in llm.PRIMARY_LADDER:
                llm._cooldowns[f"freellmapi:{m}"] = __import__("time").time() + 9999
        # whole ladder cooling -> use the healthy provider instead
        att = llm._attempts("primary")
        assert ("gemini", "gm") in [(p["name"], m) for p, m in att]
        assert all(p["name"] != "freellmapi" for p, m in att)
        # everything (incl. gemini) cooling -> last-resort ladder retry
        with llm._cd_lock:
            llm._cooldowns["gemini:gm"] = __import__("time").time() + 9999
        att = llm._attempts("primary")
        assert att and att[0][0]["name"] == "freellmapi"
        with llm._cd_lock:
            llm._cooldowns.clear()

    def test_env_ladder_override(self, monkeypatch):
        monkeypatch.setattr(llm, "_providers", _freellm_provs)
        monkeypatch.setenv("JARVIS_MODEL_LADDER", "alpha, beta")
        att = [m for p, m in llm._attempts("primary")]
        assert att[:2] == ["alpha", "beta"] and "gm" in att
