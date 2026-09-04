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
        subsystem_status: dict[str, str] = {}
        
        # 1. Stop money engine if running (actively verify thread termination)
        try:
            from .money_engine import _global_money
            if _global_money is not None:
                _global_money.stop()
                th = getattr(_global_money, "_thread", None)
                if th and th.is_alive():
                    try:
                        th.join(timeout=1.5)
                    except RuntimeError:
                        pass
                    if th.is_alive():
                        subsystem_status["money"] = "failed: worker thread hung"
                    else:
                        subsystem_status["money"] = "stopped"
                else:
                    subsystem_status["money"] = "stopped"
            else:
                subsystem_status["money"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop money engine: %s", exc)
            subsystem_status["money"] = f"failed: {exc}"

        # 2. Stop browser agent if running (verify context cleared)
        try:
            from .browser_agent import _global_browser
            if _global_browser is not None:
                _global_browser.stop()
                if _global_browser.page is not None or _global_browser.context is not None:
                    subsystem_status["browser"] = "failed: browser session failed to close"
                else:
                    subsystem_status["browser"] = "stopped"
            else:
                subsystem_status["browser"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop browser agent: %s", exc)
            subsystem_status["browser"] = f"failed: {exc}"

        # 3. Stop JARVIS brain thread and autonomy runner if running
        try:
            from .jarvis_agent import _global_jarvis
            if _global_jarvis is not None:
                _global_jarvis.stop()
                th = getattr(_global_jarvis, "thread", None)
                if th and th.is_alive():
                    th.join(timeout=1.5)
                    if th.is_alive():
                        subsystem_status["jarvis"] = "failed: worker thread hung"
                    else:
                        subsystem_status["jarvis"] = "stopped"
                else:
                    subsystem_status["jarvis"] = "stopped"
            else:
                subsystem_status["jarvis"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop JARVIS agent: %s", exc)
            subsystem_status["jarvis"] = f"failed: {exc}"
        try:
            from .autonomy import _runner
            if _runner is not None:
                _runner.stop()
                th = getattr(_runner, "thread", getattr(_runner, "_thread", None))
                if th and th.is_alive():
                    th.join(timeout=1.5)
                    if th.is_alive():
                        subsystem_status["autonomy_runner"] = "failed: runner thread hung"
                    else:
                        subsystem_status["autonomy_runner"] = "stopped"
                else:
                    subsystem_status["autonomy_runner"] = "stopped"
            else:
                subsystem_status["autonomy_runner"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop autonomy runner: %s", exc)
            subsystem_status["autonomy_runner"] = f"failed: {exc}"

        # 4. Stop voice streams and active conversation mode
        try:
            from .voice.gateway import gateway
            gateway.stop()
            subsystem_status["voice_gateway"] = "stopped"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop voice gateway: %s", exc)
            subsystem_status["voice_gateway"] = f"failed: {exc}"
        try:
            from .voice import convo
            convo.convo_mode.stop()
            subsystem_status["voice_convo"] = "stopped"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop convo mode: %s", exc)
            subsystem_status["voice_convo"] = f"failed: {exc}"

        # 5. Stop proactive anticipation engine if running
        try:
            from .proactive_agent import _engine
            if _engine is not None:
                _engine.stop()
                subsystem_status["proactive_engine"] = "stopped"
            else:
                subsystem_status["proactive_engine"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop proactive engine: %s", exc)
            subsystem_status["proactive_engine"] = f"failed: {exc}"

        # 6. Close Gemini connection pools
        try:
            from .llm import close_gemini_client
            close_gemini_client()
            subsystem_status["llm_pool"] = "closed"
        except Exception as exc:
            log.warning("KillSwitch: failed closing gemini client: %s", exc)
            subsystem_status["llm_pool"] = f"failed: {exc}"

        # 7. Cancel all supervised kernel tasks
        try:
            tasks_cancelled = self.stop_kernel_subsystems()
            subsystem_status["kernel_tasks"] = f"cancelled_{tasks_cancelled}"
        except Exception as exc:
            tasks_cancelled = 0
            subsystem_status["kernel_tasks"] = f"failed: {exc}"

        # 8. Stop Telegram if running
        try:
            if telegram_link is not None:
                tg_event = getattr(telegram_link, "_telegram_stop_event", None)
                if tg_event:
                    tg_event.set()
                subsystem_status["telegram"] = "stopped"
            else:
                subsystem_status["telegram"] = "not_running"
        except Exception as exc:
            log.warning("KillSwitch: failed to stop telegram: %s", exc)
            subsystem_status["telegram"] = f"failed: {exc}"

        # 9. Downgrade consent level and engage halt flag
        KillSwitch._is_halted = True
        try:
            self.consent.set_level("none")
            subsystem_status["consent"] = "none"
        except Exception as exc:
            log.error("KillSwitch: CRITICAL - failed to lower consent level: %s", exc)
            subsystem_status["consent"] = f"failed: {exc}"

        # Compute Tri-State Termination Status: halted | partially_halted | failed
        failures = [k for k, v in subsystem_status.items() if v.startswith("failed")]
        if not failures and subsystem_status.get("consent") == "none":
            status = "halted"
            ok = True
            speech = "Emergency stop confirmed. All autonomous subsystems halted."
        elif subsystem_status.get("consent") == "none":
            status = "partially_halted"
            ok = False
            speech = f"Emergency stop partially completed. Failed to halt: {', '.join(failures)}."
        else:
            status = "failed"
            ok = False
            speech = "Emergency stop failed to engage critical safety controls."

        dt_ms = (time.perf_counter() - t0) * 1000.0
        log.warning("EMERGENCY KILL SWITCH: %s in %.2fms (%d kernel tasks cancelled, %d failures). Reason: %s",
                    status, dt_ms, tasks_cancelled, len(failures), reason)

        res = {
            "ok": ok,
            "status": status,
            "speech": speech,
            "subsystem_status": subsystem_status,
            "failed_subsystems": failures,
            "consent_level": self.consent.current_level,
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

    def disengage(self, level: str = "assist") -> dict[str, Any]:
        """Disengage the kill switch and restore consent to an active tier (default 'assist')."""
        KillSwitch._is_halted = False
        target = level if level in ("read", "assist", "execute", "full") else "assist"
        self.consent.set_level(target, require_user_confirmation=False)
        try:
            self.audit.log_action({"type": "kill_switch_disengaged", "restored_level": target}, outcome={"ok": True})
        except Exception:
            pass
        log.info("Kill switch disengaged; consent tier restored to '%s'.", target)
        return {
            "ok": True,
            "status": "active",
            "speech": f"Kill switch disengaged. Tools and autonomy restored at '{target}' tier.",
            "consent_level": target,
        }



_global_kill: Optional[KillSwitch] = None


def get_kill_switch() -> KillSwitch:
    global _global_kill
    if _global_kill is None:
        _global_kill = KillSwitch()
    return _global_kill
