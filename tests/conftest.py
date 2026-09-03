"""Global hermeticity: shared singletons must not leak between tests."""
import pytest


@pytest.fixture(autouse=True)
def clean_shared_state(monkeypatch):
    from mk2 import errlog, llm

    # cascade cooldowns, measured speeds + error ring are process-wide
    with llm._cd_lock:
        llm._cooldowns.clear()
        llm._ttft.clear()
    errlog.clear()

    # style classification must never make real network calls in tests;
    # individual tests override classify explicitly when they need a tone.
    try:
        import mk2.style_controller as sc

        monkeypatch.setattr(sc, "classify", lambda t: {"tone": "neutral"},
                            raising=False)
    except Exception:
        pass

    # reset consent level and kill switch state to baseline assist
    try:
        from mk2.kill_switch import KillSwitch, get_kill_switch
        KillSwitch._is_halted = False
        from mk2.consent import get_consent_manager
        cm = get_consent_manager()
        cm.current_level = "assist"
    except Exception:
        pass

    yield

    # restore after test completes
    try:
        from mk2.kill_switch import KillSwitch
        KillSwitch._is_halted = False
        from mk2.consent import get_consent_manager
        get_consent_manager().current_level = "assist"
    except Exception:
        pass
