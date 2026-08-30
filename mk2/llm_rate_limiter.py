"""LLM Rate Limiter for Autonomous Loops (JARVIS Task 10).

Prevents runaway LLM API calls and quota depletion by throttling automated agent loop calls.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger("mk2.llm_rate_limiter")


class LLMRateLimiter:
    """Sliding window rate limiter for autonomous LLM invocations."""

    def __init__(self, max_calls_per_minute: int = 20):
        self.max_calls = max_calls_per_minute
        self.calls: list[float] = []

    def allow(self) -> bool:
        """Return True if an LLM call is allowed under the rate limit, else False."""
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.max_calls:
            log.warning("LLM rate limit reached (%d calls in past 60s). Throttling call.", len(self.calls))
            return False
        self.calls.append(now)
        return True


_global_rate_limiter: Optional[LLMRateLimiter] = None


def get_llm_rate_limiter() -> LLMRateLimiter:
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = LLMRateLimiter(max_calls_per_minute=20)
    return _global_rate_limiter
