"""Proactive Intelligence Engine for EVO MK2 (JARVIS Opportunity #2 & #35).

Anticipates upcoming user routines, meetings, and habits using Bayesian temporal patterns
and issues gentle proactive notifications before the user has to ask.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import bus, patterns

log = logging.getLogger("mk2.proactive")


class ProactiveEngine:
    def __init__(self, check_interval_secs: int = 60) -> None:
        self.interval = check_interval_secs
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="mk2-proactive-anticipator")
            self._thread.start()
            log.info("Proactive anticipation engine started")

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.check_and_anticipate()
            except Exception as exc:
                log.debug("Proactive engine check error: %s", exc)
            self._stop_event.wait(self.interval)

    def check_and_anticipate(self) -> list[dict[str, Any]]:
        """Identify upcoming routines and dispatch notifications."""
        from .kill_switch import get_kill_switch
        if get_kill_switch().is_active():
            return []

        upcoming = patterns.predict_upcoming_patterns(lookahead_minutes=25)
        dispatched = []
        for pat in upcoming:
            mins_left = pat.get("minutes_until", 0)
            hint = pat.get("action_hint", pat.get("type", "routine task"))
            if mins_left <= 5:
                msg = f"Heads up, sir: usually around this time you {hint}."
            else:
                msg = f"In about {mins_left} minutes: {hint} scheduled."
            bus.publish("notify.out", {
                "kind": "proactive_anticipation",
                "text": msg,
                "pattern_id": pat.get("id"),
            })
            patterns.mark_pattern_triggered(pat["id"])
            dispatched.append({"pattern": pat, "message": msg})
        return dispatched


_engine: ProactiveEngine | None = None


def get_proactive_engine() -> ProactiveEngine:
    global _engine
    if _engine is None:
        _engine = ProactiveEngine()
    return _engine
