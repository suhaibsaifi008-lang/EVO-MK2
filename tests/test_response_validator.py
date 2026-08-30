import pytest
from mk2 import response_validator


def test_validator_clean_text():
    clean = "The server is online and running normally on port 8421."
    ok, res = response_validator.validate_response(clean)
    assert ok
    assert res == clean


def test_validator_catches_ai_tropes():
    bad = "As an AI language model, I can help you with: 1. Code 2. Research"
    ok, res = response_validator.validate_response(bad)
    assert ok
    assert "As an AI" not in res
    assert "language model" not in res
