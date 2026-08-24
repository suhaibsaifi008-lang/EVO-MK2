"""One-call self-diagnosis: every subsystem reports OK/DOWN + detail.

This is how failures get pinpointed instantly instead of guessed at.
"""
import time
from pathlib import Path

from . import db, errlog, llm
from .config import DATA, settings


def _check(name: str, fn) -> dict:
    t0 = time.perf_counter()
    try:
        detail = fn()
        return {"component": name, "ok": True, "detail": str(detail)[:160],
                "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"component": name, "ok": False,
                "detail": f"{type(exc).__name__}: {exc}"[:200],
                "ms": round((time.perf_counter() - t0) * 1000)}


def run_checks(include_network: bool = False) -> dict:
    checks = []

    def db_ok():
        db.get_setting("ping")
        return "ok"

    def db_ok():
        db.get_setting("ping")
        return "ok"

    def vault_ok():
        d = DATA / "vault"
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return "writable"

    def shots_ok():
        d = DATA / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def stt_ok():
        from .voice import stt as stt_mod

        p = stt_mod.VOSK_DIR
        if not (p.exists() and any(p.iterdir())):
            raise RuntimeError("vosk model not downloaded")
        return str(p)

    def tools_ok():
        from . import tools as t

        n = t.ensure_loaded()
        if n == 0:
            raise RuntimeError("no tools registered")
        return f"{n} registered"

    def llm_probe():
        llm.chat([{"role": "user", "content": "ping"}], temperature=0,
                 timeout=6, bias=False, max_providers=1)
        return "reachable"

    checks.append(_check("database", db_ok))
    checks.append(_check("vault", vault_ok))
    checks.append(_check("screens_dir", shots_ok))
    if include_network:
        checks.append(_check("stt_model", stt_ok))
        checks.append(_check("llm_reachable", llm_probe))

    providers = [p["name"] for p in llm._providers()]
    errors = errlog.recent(10)
    down = [c for c in checks if not c["ok"]]
    router = llm.diagnostics()
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "providers": providers,
        "router": {"ladders": router["ladders"],
                   "cooling_down": router["cooling_down"],
                   "measured_ttft_s": router.get("measured_ttft_s", {})},
        "recent_errors": errors,
    }
