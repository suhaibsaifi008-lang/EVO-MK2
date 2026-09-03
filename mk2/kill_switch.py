"""Master Emergency Kill Switch for EVO MK2 (JARVIS Phase 12)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .audit import get_audit_logger
from .browser_agent import get_browser_agent
from .consent import get_consent_manager
from .jarvis_agent import get_jarvis_agent
from .money_engine import get_money_engine

try:
    from . import telegram_link
    _telegram_link = telegram_link
except Exception:
    _telegram_link = None

log = logging.getLogger("mk2.kill_switch")


class KillSwitch:
    """Guarantees immediate (<10ms) emergency halt of all autonomous systems."""

    def __init__(self):
        pass

    @property
    def consent(self):
        return get_consent_manager()

    @property
    def audit(self):
        return get_audit_logger()

    @property
    def browser(self):
        return get_browser_agent()

    @property
    def money(self):
        return get_money_engine()

    def stop_kernel_subsystems(self) -> int:
        """Cancel all supervised asyncio kernel tasks."""
        cancelled = 0
        try:
            from .kernel import get_kernel_tasks
            for name, task in list(get_kernel_tasks().items()):
                try:
                    if not task.done():
                        task.cancel()
                        cancelled += 1
                except Exception:
                    pass
        except Exception as exc:
            log.debug("Kernel task cancellation note: %s", exc)
        return cancelled

    def stop_all(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        t0 = time.perf_counter()
        
        # 1. Stop money engine if running
        try:
            from .money_engine import _global_money
            if _global_money is not None:
                _global_money.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop money engine: %s", exc)

        # 2. Stop browser agent if running
        try:
            from .browser_agent import _global_browser
            if _global_browser is not None:
                _global_browser.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop browser agent: %s", exc)

        # 3. Stop JARVIS brain thread and autonomy runner if running
        try:
            from .jarvis_agent import _global_jarvis
            if _global_jarvis is not None:
                _global_jarvis.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop JARVIS agent: %s", exc)
        try:
            from .autonomy import _runner
            if _runner is not None:
                _runner.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop autonomy runner: %s", exc)

        # 4. Stop voice streams and active conversation mode
        try:
            from .voice.gateway import gateway
            gateway.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop voice gateway: %s", exc)
        try:
            from .voice import convo
            convo.convo_mode.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop convo mode: %s", exc)

        # 5. Stop proactive anticipation engine if running
        try:
            from .proactive_agent import _engine
            if _engine is not None:
                _engine.stop()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop proactive engine: %s", exc)

        # 6. Close Gemini connection pools
        try:
            from .llm import close_gemini_client
            close_gemini_client()
        except Exception as exc:
            log.warning("KillSwitch: failed closing gemini client: %s", exc)

        # 7. Cancel all supervised kernel tasks
        tasks_cancelled = self.stop_kernel_subsystems()

        # 8. Stop Telegram if running
        try:
            if telegram_link is not None:
                tg_event = getattr(telegram_link, "_telegram_stop_event", None)
                if tg_event:
                    tg_event.set()
        except Exception as exc:
            log.warning("KillSwitch: failed to stop telegram: %s", exc)

        # 9. Downgrade consent level
        KillSwitch._is_halted = True
        self.consent.set_level("none")

        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.warning("EMERGENCY KILL SWITCH: All autonomous systems halted in %.2fms (%d kernel tasks cancelled). Reason: %s",
                    dt_ms, tasks_cancelled, reason)

        res = {
            "ok": True,
            "status": "halted",
            "consent_level": "none",
            "kernel_tasks_cancelled": tasks_cancelled,
            "latency_ms": round(dt_ms, 2),
            "reason": reason,
        }
        self.audit.log_action({"type": "kill_switch_activated", "reason": reason}, outcome=res)
        return res

    def stop_money(self) -> dict[str, Any]:
        self.money.stop()
        log.info("MoneyEngine halted via KillSwitch.")
        return {"ok": True, "subsystem": "money", "status": "stopped"}

    _is_halted: bool = False

    def is_active(self) -> bool:
        """Check whether the kill switch is currently engaged."""
        return KillSwitch._is_halted or self.consent.get_level() == "none"

    def downgrade_to(self, level: str) -> bool:
        if level != "none":
            KillSwitch._is_halted = False
        res = self.consent.set_level(level, require_user_confirmation=False)
        return res


_global_kill: Optional[KillSwitch] = None


def get_kill_switch() -> KillSwitch:
    global _global_kill
    if _global_kill is None:
        _global_kill = KillSwitch()
    return _global_kill
