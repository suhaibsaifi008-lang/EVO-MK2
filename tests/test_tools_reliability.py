import pytest
import time
from mk2 import tools


def test_classify_error():
    assert tools.classify_error("Connection timeout while reaching server") == "network"
    assert tools.classify_error("HTTP 403 Forbidden: unauthorized access") == "permission"
    assert tools.classify_error("File or table not found 404") == "not_found"
    assert tools.classify_error("Rate limit exceeded 429 too many requests") == "rate_limit"
    assert tools.classify_error("Invalid payload format") == "invalid"
    assert tools.classify_error("Something weird happened") == "unknown"


def test_circuit_breaker_tripping():
    name = "test_breaker_tool"
    tools.tool(name, "test tool", {}, permission="read")(lambda: {"ok": True, "speech": "ok"})

    # Reset state
    tools._CIRCUIT_BREAKER.pop(name, None)
    tools._CONSECUTIVE_FAILS[name] = tools._BREAKER_THRESHOLD
    tools._CIRCUIT_BREAKER[name] = time.time() + 60.0

    res = tools.call(name, {})
    assert not res.get("ok")
    assert "temporarily disabled" in res.get("speech", "") or "circuit" in res.get("speech", "")
    assert res.get("data", {}).get("circuit_open") is True

    # Cleanup
    tools._CIRCUIT_BREAKER.pop(name, None)
    tools._CONSECUTIVE_FAILS.pop(name, None)
