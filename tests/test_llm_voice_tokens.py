import pytest
from mk2 import llm


def test_estimate_tokens():
    msgs = [
        {"role": "system", "content": "You are JARVIS."},
        {"role": "user", "content": "What is the weather today in New York?"},
    ]
    toks = llm.estimate_tokens(msgs)
    assert toks > 5
    assert toks < 30


def test_voice_ladder_presence():
    assert "voice" in llm.ROLES
    assert "voice" in llm.MODEL_LADDERS
    assert len(llm.VOICE_LADDER) >= 3


def test_providers_voice_ordering():
    provs = llm._providers("voice")
    assert isinstance(provs, list)
    assert len(provs) > 0
