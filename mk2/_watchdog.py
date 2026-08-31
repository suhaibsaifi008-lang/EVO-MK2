"""Watchdog: monitors subsystems, auto-restarts with backoff, publishes health.

Replaces the naive _supervise() with a proper watchdog that:
- Tracks heartbeat per subsystem
- Detects memory/thread anomalies
- Exponential backoff on restart
- Publishes health status to the event bus
- Notifies user via voice/UI on degradation
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Coroutine

from .bus import bus

log = logging.getLogger("mk2.watchdog")

# Tunables (env-overridable)
_WATCHDOG_HEARTBEAT_S = int(os.environ.get("EVO_WD_HEARTBEAT", "5"))
_WATCHDOG_STALL_S = int(os.environ.get("EVO_WD_STALL", "30"))
_WATCHDOG_MAX_RESTARTS = int(os.environ.get("EVO_WD_MAX_RESTARTS", "5"))
_WATCHDOG_BACKOFF_BASE = float(os.environ.get("EVO_WD_BACKOFF", "2.0"))
_WATCHDOG_BACKOFF_CAP = float(os.environ.get("EVO_WD_BACKOFF_CAP", "60.0"))
_WATCHDOG_MEM_LIMIT_MB = int(os.environ.get("EVO_WD_MEM_MB", "1024"))
_WATCHDOG_THREAD_LIMIT = int(os.environ.get("EVO_WD_THREADS", "80"))
_JOURNAL_INTERVAL = int(os.environ.get("EVO_JOURNAL_INTERVAL", "1"))


@dataclass
class SubSystem:
    name: str
    factory: Callable[[], Coroutine]
    task: asyncio.Task | None = None
    restarts: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)
    backoff: float = _WATCHDOG_BACKOFF_BASE
    degraded: bool = False
    stall_alerted: bool = False


class Watchdog:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._subs: dict[str, SubSystem] = {}
        self._running = False
        self._health_task: asyncio.Task | None = None
        self._journal_task: asyncio.Task | None = None
        self._procinfo = self._get_process_info()

    def register(self, name: str, factory: Callable[[], Coroutine]) -> asyncio.Task:
        sub = SubSystem(name=name, factory=factory)
        self._subs[name] = sub
        sub.task = self._loop.create_task(self._runner(sub), name=f"wd-{name}")
        return sub.task

    async def start(self) -> None:
        self._running = True
        self._health_task = self._loop.create_task(
            self._health_loop(), name="wd-health")
        self._journal_task = self._loop.create_task(
            self._journal_loop(), name="wd-journal")
        log.info("watchdog started: %d subsystem(s)", len(self._subs))

    async def shutdown(self) -> None:
        self._running = False
        for sub in self._subs.values():
            if sub.task and not sub.task.done():
                sub.task.cancel()
        if self._health_task:
            self._health_task.cancel()
        if self._journal_task:
            self._journal_task.cancel()
        log.info("watchdog shut down")

    async def _runner(self, sub: SubSystem) -> None:
        while True:
            try:
                sub.last_heartbeat = time.monotonic()
                await sub.factory()
            except asyncio.CancelledError:
                log.info("subsystem %s cancelled", sub.name)
                return
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                sub.restarts += 1
                sub.backoff = min(
                    _WATCHDOG_BACKOFF_BASE * (2 ** (sub.restarts - 1)),
                    _WATCHDOG_BACKOFF_CAP,
                )
                log.warning(
                    "subsystem %s died (%s) restart #%d backoff=%.1fs",
                    sub.name, exc, sub.restarts, sub.backoff,
                )
                try:
                    bus.publish("watchdog.restart", {
                        "subsystem": sub.name,
                        "crashes": sub.restarts,
                        "error": str(exc)[:200],
                    })
                except Exception:
                    pass
                if sub.restarts >= _WATCHDOG_MAX_RESTARTS:
                    sub.degraded = True
                    log.error(
                        "subsystem %s degraded after %d crashes",
                        sub.name, sub.restarts,
                    )
                    try:
                        bus.publish("system.degraded", {
                            "subsystem": sub.name,
                            "crashes": sub.restarts,
                            "status": "degraded",
                        })
                    except Exception:
                        pass
                    await asyncio.sleep(min(sub.backoff * 2, _WATCHDOG_BACKOFF_CAP * 2))
                    continue
                await asyncio.sleep(sub.backoff)

    async def _health_loop(self) -> None:
        while self._running:
            try:
                await self._check_health()
            except Exception as exc:
                log.debug("health check error: %s", exc)
            await asyncio.sleep(_WATCHDOG_HEARTBEAT_S)

    async def _check_health(self) -> None:
        now = time.monotonic()
        mem_mb = 0.0
        n_threads = 0
        cpu_pct = 0.0

        # Resource usage
        try:
            import psutil
            p = psutil.Process()
            mem_mb = p.memory_info().rss / (1024 * 1024)
            cpu_pct = p.cpu_percent()
        except Exception:
            try:
                import resource as _res
                r = _res.getrusage(_res.RUSAGE_SELF)
                mem_mb = r.ru_maxrss / 1024
            except Exception:
                pass
        try:
            import threading as _thr
            n_threads = _thr.active_count()
        except Exception:
            pass

        if mem_mb > _WATCHDOG_MEM_LIMIT_MB:
            log.warning(
                "memory usage high: %.1f MB (limit %d MB)",
                mem_mb, _WATCHDOG_MEM_LIMIT_MB,
            )
            try:
                bus.publish("system.warning", {
                    "kind": "memory",
                    "value_mb": round(mem_mb, 1),
                    "limit_mb": _WATCHDOG_MEM_LIMIT_MB,
                })
            except Exception:
                pass

        if n_threads > _WATCHDOG_THREAD_LIMIT:
            log.warning(
                "thread count high: %d (limit %d)",
                n_threads, _WATCHDOG_THREAD_LIMIT,
            )
            try:
                bus.publish("system.warning", {
                    "kind": "threads",
                    "value": n_threads,
                    "limit": _WATCHDOG_THREAD_LIMIT,
                })
            except Exception:
                pass

        for name, sub in self._subs.items():
            stall = (now - sub.last_heartbeat) > _WATCHDOG_STALL_S
            if stall and not sub.stall_alerted:
                sub.stall_alerted = True
                log.error(
                    "subsystem %s stalled (no heartbeat >%.0fs)",
                    name, _WATCHDOG_STALL_S,
                )
                try:
                    bus.publish("system.warning", {
                        "kind": "stall",
                        "subsystem": name,
                        "silence_s": round(now - sub.last_heartbeat, 1),
                    })
                except Exception:
                    pass
            elif not stall:
                sub.stall_alerted = False

        try:
            bus.publish("system.health", {
                "ts": time.time(),
                "mem_mb": round(mem_mb, 1),
                "threads": n_threads,
                "cpu_pct": round(cpu_pct, 1),
                "subsystems": {
                    n: {
                        "restarts": s.restarts,
                        "degraded": s.degraded,
                        "age_s": round(now - s.last_heartbeat, 1),
                    }
                    for n, s in self._subs.items()
                },
            })
        except Exception:
            pass

    def heartbeat(self, name: str) -> None:
        if name in self._subs:
            self._subs[name].last_heartbeat = time.monotonic()

    async def _journal_loop(self) -> None:
        from . import db
        while self._running:
            try:
                pass
            except Exception as exc:
                log.debug("journal error: %s", exc)
            await asyncio.sleep(_JOURNAL_INTERVAL)

    @staticmethod
    def _get_process_info() -> dict:
        try:
            return {"pid": os.getpid()}
        except Exception:
            return {}


_watchdog: Watchdog | None = None


def get_watchdog() -> Watchdog | None:
    return _watchdog


def init_watchdog(loop: asyncio.AbstractEventLoop) -> Watchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = Watchdog(loop)
    return _watchdog
