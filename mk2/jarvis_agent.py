"""The Master JARVIS Brain for EVO MK2 (JARVIS Phase 10)."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from .comms_agent import get_comms_agent
from .comms_intelligence import get_comms_intelligence
from .consent import get_consent_manager
from .email_agent import get_email_agent
from .ethics import get_moral_engine
from .knowledge_agent import get_knowledge_agent
from .money_engine import get_money_engine
from .research_agent import get_research_agent
from .schedule_agent import get_schedule_agent
from .security_agent import get_security_agent
from .synthesis import get_synthesis_engine
from .wellness_agent import get_wellness_agent

log = logging.getLogger("mk2.jarvis_agent")


class JarvisAgent:
    """The proactive intelligence core of EVO MK2."""

    def __init__(self, tick_interval: int = 60):
        self.tick_interval = tick_interval
        self.running = False
        self.thread: Optional[threading.Thread] = None

        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.schedule = get_schedule_agent()
        self.email = get_email_agent()
        self.comms = get_comms_agent()
        self.comms_intel = get_comms_intelligence()
        self.knowledge = get_knowledge_agent()
        self.research = get_research_agent()
        self.security = get_security_agent()
        self.wellness = get_wellness_agent()
        self.money = get_money_engine()
        self.synthesis = get_synthesis_engine()

        self.last_tick_ts = 0.0
        self.proactive_alerts: list[str] = []

    def start(self) -> bool:
        if self.running:
            return True
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="JarvisBrain")
        self.thread.start()
        log.info("JarvisAgent master proactive loop started.")
        return True

    def stop(self) -> None:
        self.running = False
        log.info("JarvisAgent master proactive loop stopped.")

    def _loop(self) -> None:
        while self.running:
            try:
                self.tick()
            except Exception as exc:
                log.error("JarvisAgent tick error: %s", exc)

            slept = 0
            while self.running and slept < self.tick_interval:
                time.sleep(2)
                slept += 2

    def tick(self) -> dict[str, Any]:
        self.last_tick_ts = time.time()
        findings: list[str] = []

        # 1. Calendar & Pre-meeting Prep
        upcoming = self.schedule.get_upcoming_events(hours=2)
        if upcoming:
            prep = self.schedule.pre_meeting_prep(upcoming[0])
            if prep.get("prep_ready"):
                findings.append(f"Meeting prep ready for '{upcoming[0].get('title')}'")

        # 2. Wellness check
        w_res = self.wellness.suggest_break()
        if w_res.verdict == "caution":
            findings.append(w_res.reasoning)

        # 3. Money scan
        if self.consent.has_consent("autonomy_execute"):
            m_res = self.money.tick()
            if m_res.get("enqueued_id"):
                findings.append(f"Enqueued opportunity proposal #{m_res['enqueued_id']}")

        self.proactive_alerts = findings[-10:]
        return {"ok": True, "ts": self.last_tick_ts, "findings": findings}


_global_jarvis: Optional[JarvisAgent] = None


def get_jarvis_agent() -> JarvisAgent:
    global _global_jarvis
    if _global_jarvis is None:
        _global_jarvis = JarvisAgent()
    return _global_jarvis
