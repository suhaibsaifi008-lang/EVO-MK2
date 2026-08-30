"""LLM Rate Limiter for Autonomous Loops (JARVIS Fix B).

Prevents runaway API calls and quota depletion by throttling automated loop calls.
"""
from __future__ import annotations

import time
from typing import Optional


class LLMRateLimiter:
    """Sliding window rate limiter for autonomous LLM invocations."""

    def __init__(self, max_calls_per_minute: int = 20):
        self.max_calls = max_calls_per_minute
        self.calls: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True


_global_limiter: Optional[LLMRateLimiter] = None


def get_llm_rate_limiter() -> LLMRateLimiter:
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = LLMRateLimiter()
    return _global_limiter
