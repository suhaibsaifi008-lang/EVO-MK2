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

    def stop_all(self, reason: str = "User triggered emergency stop") -> dict[str, Any]:
        t0 = time.perf_counter()
        self.money.stop()
        self.browser.stop()
        self.consent.set_level("assist")
        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.warning("EMERGENCY KILL SWITCH: All autonomous systems halted in %.2fms. Reason: %s", dt_ms, reason)

        res = {
            "ok": True,
            "status": "halted",
            "consent_level": "assist",
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
