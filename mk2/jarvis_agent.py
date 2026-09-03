"""The Master JARVIS Brain for EVO MK2 (JARVIS Phase 10 / Tasks 4, 5, 6)."""
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
from .financial_intelligence import get_financial_intelligence
from .knowledge_agent import get_knowledge_agent
from .llm_rate_limiter import get_llm_rate_limiter
from .money_engine import get_money_engine
from .research_agent import get_research_agent
from .schedule_agent import get_schedule_agent
from .security_agent import get_security_agent
from .self_improvement import get_self_improvement_engine
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
        self.finance = get_financial_intelligence()
        self.self_improve = get_self_improvement_engine()
        self.rate_limiter = get_llm_rate_limiter()

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

    def _check_research_topics(self) -> list[str]:
        """Check if any monitored topics have fresh developments."""
        try:
            return self.research.check_monitored_topics()
        except Exception as exc:
            log.debug("Research topic check error: %s", exc)
            return []

    def _load_daily_state(self) -> dict:
        import json
        from .config import DATA
        _STATE_FILE = DATA / "jarvis_state.json"
        if _STATE_FILE.exists():
            try:
                return json.loads(_STATE_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_daily_state(self, state: dict) -> None:
        import json
        from .config import DATA
        _STATE_FILE = DATA / "jarvis_state.json"
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state))

    def _should_run_daily(self, check_name: str) -> bool:
        """Run a check at most once per day (86400 seconds)."""
        state = self._load_daily_state()
        last_run = state.get(f"_last_{check_name}_ts", 0.0)
        now = time.time()
        if now - last_run < 86400:
            return False
        state[f"_last_{check_name}_ts"] = now
        self._save_daily_state(state)
        return True


    def tick(self) -> dict[str, Any]:
        self.last_tick_ts = time.time()
        findings: list[str] = []

        # 1. Calendar & Pre-meeting Prep
        upcoming = self.schedule.get_upcoming_events(hours=2)
        if upcoming:
            if self.rate_limiter.allow():
                prep = self.schedule.pre_meeting_prep(upcoming[0])
                if prep.get("prep_ready"):
                    findings.append(f"Meeting prep ready for '{upcoming[0].get('title')}'")

        # 2. Wellness check
        w_res = self.wellness.suggest_break()
        if w_res.verdict == "caution":
            findings.append(w_res.reasoning)

        # 3. Research topic developments
        r_alerts = self._check_research_topics()
        if r_alerts:
            findings.extend(r_alerts)

        # 4. Proactive cross-domain suggestion
        if self.rate_limiter.allow():
            try:
                sugg = self.synthesis.proactive_suggestion()
                if sugg:
                    findings.append(f"JARVIS Suggestion: {sugg}")
            except Exception as exc:
                log.debug("Proactive suggestion error: %s", exc)

        # 5. Financial briefing (once per day)
        if self._should_run_daily("financial_briefing"):
            try:
                briefing = self.finance.financial_briefing()
                if briefing and len(briefing) > 50:
                    findings.append(f"Financial briefing: {briefing[:100]}...")
            except Exception as exc:
                log.debug("Daily financial briefing note: %s", exc)

        # 6. Self-improvement scan (once per day)
        if self._should_run_daily("self_improvement"):
            try:
                issues = self.self_improve.analyze_codebase()
                if issues:
                    high_priority = [i for i in issues if i.get("severity") in ("critical", "high")]
                    if high_priority:
                        findings.append(f"Self-improvement: {len(high_priority)} code improvements identified")
            except Exception as exc:
                log.debug("Daily self-improvement scan note: %s", exc)

        # 7. Money scan
        if self.consent.has_consent("autonomy_execute"):
            m_res = self.money.tick()
            if m_res.get("enqueued_id"):
                findings.append(f"Enqueued opportunity proposal #{m_res['enqueued_id']}")

        # 8. Memory graph consolidation (once per day)
        if self._should_run_daily("memory_consolidation"):
            try:
                from . import deep_memory
                c_res = deep_memory.consolidate_memories()
                if c_res.get("edges_added", 0) > 0:
                    findings.append(f"Memory Consolidation: extracted {c_res['edges_added']} relationship triples")
            except Exception as exc:
                log.debug("Daily memory consolidation note: %s", exc)

        self.proactive_alerts = findings[-10:]
        return {"ok": True, "ts": self.last_tick_ts, "findings": findings}


_global_jarvis: Optional[JarvisAgent] = None


def get_jarvis_agent() -> JarvisAgent:
    global _global_jarvis
    if _global_jarvis is None:
        _global_jarvis = JarvisAgent()
    return _global_jarvis
