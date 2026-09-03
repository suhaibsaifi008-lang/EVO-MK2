"""Chaos & Failure Injection Harness for EVO MK2 (M8.2).

Provides scoped, controlled failure injection to verify resilient recovery:
  1. Network: socket/HTTP timeouts & connection drops
  2. Model: LLM provider 429 quota exhaustion & 500 server drops
  3. Tool: runtime crashes & synthetic tool timeouts
  4. Database: SQLite busy / locked errors
  5. Browser: Playwright session disconnect / target crashes
  6. Telegram: polling disconnects & webhook failures
  7. Voice: audio stream drops & TTS synth failures
  8. Subprocess: hanging loops & process kill signals
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import Generator, Optional
from unittest.mock import MagicMock, patch

log = logging.getLogger("mk2.chaos")


class ChaosEngine:
    """Scoped fault injection controller for resilience verification."""

    @staticmethod
    @contextlib.contextmanager
    def kill_network() -> Generator[None, None, None]:
        """Simulate total network blackout (DNS failure / connection refused)."""
        import urllib.request
        def failing_open(*args, **kwargs):
            raise ConnectionRefusedError("Chaos: Network interface down (simulated)")

        with patch.object(urllib.request, "urlopen", side_effect=failing_open):
            log.info("Chaos injected: Network interface killed")
            yield
        log.info("Chaos recovered: Network interface restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_model(status_code: int = 429) -> Generator[None, None, None]:
        """Simulate LLM provider outage or rate-limit exhaustion."""
        from . import llm
        def failing_chat(*args, **kwargs):
            if kwargs.get("evaluation_mode") == "execution_critical":
                raise llm.CriticalEvaluationUnavailable(f"Chaos: LLM Provider HTTP {status_code} Quota Exceeded (simulated)")
            raise RuntimeError(f"Chaos: LLM Provider HTTP {status_code} Quota Exceeded (simulated)")

        with patch.object(llm, "chat", side_effect=failing_chat):
            log.info("Chaos injected: LLM provider killed (HTTP %d)", status_code)
            yield
        log.info("Chaos recovered: LLM provider restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_tool(tool_name: str) -> Generator[None, None, None]:
        """Force a specific tool to raise an unexpected runtime exception."""
        from . import tools
        target = tools._REGISTRY.get(tool_name)
        if not target:
            raise ValueError(f"Unknown tool: {tool_name}")

        orig_fn = target.fn
        def crashing_fn(*args, **kwargs):
            raise RuntimeError(f"Chaos: Tool {tool_name} suffered fatal crash (simulated)")

        target.fn = crashing_fn
        log.info("Chaos injected: Tool %s killed", tool_name)
        try:
            yield
        finally:
            target.fn = orig_fn
            log.info("Chaos recovered: Tool %s restored", tool_name)

    @staticmethod
    @contextlib.contextmanager
    def kill_database() -> Generator[None, None, None]:
        """Simulate SQLite locked / busy deadlock."""
        from . import db
        def failing_connect():
            raise sqlite3.OperationalError("Chaos: database is locked (simulated)")

        with patch.object(db, "connect", side_effect=failing_connect):
            log.info("Chaos injected: Database locked")
            yield
        log.info("Chaos recovered: Database restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_browser() -> Generator[None, None, None]:
        """Simulate headless browser crash."""
        from . import autonomy
        def failing_navigate(*args, **kwargs):
            return {"ok": False, "speech": "Browser crashed unexpectedly.", "data": {"crashed": True}}

        with patch.object(autonomy.BrowserAgent, "navigate", side_effect=failing_navigate):
            log.info("Chaos injected: Browser agent crashed")
            yield
        log.info("Chaos recovered: Browser agent restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_telegram() -> Generator[None, None, None]:
        """Simulate Telegram bot network disconnect."""
        from . import telegram_link
        def failing_send(*args, **kwargs):
            return {"ok": False, "error": "Chaos: Telegram network unreachable"}

        with patch.object(telegram_link, "send_message", side_effect=failing_send):
            log.info("Chaos injected: Telegram link dropped")
            yield
        log.info("Chaos recovered: Telegram link restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_voice() -> Generator[None, None, None]:
        """Simulate soundcard / TTS engine failure."""
        from .voice import tts
        def failing_speak(self, text, stop):
            raise OSError("Chaos: Audio device disconnected (simulated)")

        with patch.object(tts.Speaker, "_speak", side_effect=failing_speak):
            log.info("Chaos injected: Audio hardware dropped")
            yield
        log.info("Chaos recovered: Audio hardware restored")

    @staticmethod
    @contextlib.contextmanager
    def kill_subprocess(timeout_s: float = 1.0) -> Generator[None, None, None]:
        """Configure engineering sandbox to trigger fast subprocess timeout kills."""
        from . import engineering
        orig_timeout = getattr(engineering.EngineeringWorkspace, "DEFAULT_TIMEOUT", 30)
        engineering.EngineeringWorkspace.DEFAULT_TIMEOUT = timeout_s
        log.info("Chaos injected: Subprocess timeout reduced to %.1fs", timeout_s)
        try:
            yield
        finally:
            engineering.EngineeringWorkspace.DEFAULT_TIMEOUT = orig_timeout
            log.info("Chaos recovered: Subprocess timeout restored to %ds", orig_timeout)


# Singleton
chaos = ChaosEngine()
