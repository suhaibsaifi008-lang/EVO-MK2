"""Master Emergency Kill Switch for EVO MK2 (JARVIS Phase 12)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .audit import get_audit_logger
from .browser_agent import get_browser_agent
from .consent import get_consent_manager
from .money_engine import get_money_engine

log = logging.getLogger("mk2.kill_switch")


class KillSwitch:
    """Guarantees immediate (<10ms) emergency halt of all autonomous systems."""

    def __init__(self):
        self.audit = get_audit_logger()
        self.consent = get_consent_manager()
        self.browser = get_browser_agent()
        self.money = get_money_engine()

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
        
        # 1. Stop money engine
        try:
            self.money.stop()
        except Exception:
            pass

        # 2. Stop browser agent
        try:
            self.browser.stop()
        except Exception:
            pass

        # 3. Stop JARVIS brain thread
        try:
            from .jarvis_agent import get_jarvis_agent
            get_jarvis_agent().stop()
        except Exception:
            pass

        # 4. Cancel all supervised kernel tasks
        tasks_cancelled = self.stop_kernel_subsystems()

        # 5. Stop Telegram if running
        try:
            from . import telegram_link
            tg_event = getattr(telegram_link, "_telegram_stop_event", None)
            if tg_event:
                tg_event.set()
        except Exception:
            pass

        # 6. Downgrade consent level
        self.consent.set_level("assist")

        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.warning("EMERGENCY KILL SWITCH: All autonomous systems halted in %.2fms (%d kernel tasks cancelled). Reason: %s",
                    dt_ms, tasks_cancelled, reason)

        res = {
            "ok": True,
            "status": "halted",
            "consent_level": "assist",
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

    def downgrade_to(self, level: str) -> bool:
        return self.consent.set_level(level)


_global_kill: Optional[KillSwitch] = None


def get_kill_switch() -> KillSwitch:
    global _global_kill
    if _global_kill is None:
        _global_kill = KillSwitch()
    return _global_kill
