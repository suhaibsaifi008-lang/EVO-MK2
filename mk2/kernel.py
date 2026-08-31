"""Kernel: owns the loop, supervises subsystems, restarts on death."""
import asyncio
import logging
import os
import threading
import sys
from typing import Any

from . import config, db, llm, tools
from .bus import bus

log = logging.getLogger("mk2.kernel")


def _log_event(subsystem: str, event: str, **kwargs):
    log.info("[%s] %s %s", subsystem, event, " ".join(f"{k}={v}" for k, v in kwargs.items()))


_tasks: dict[str, asyncio.Task] = {}
_global_kernel_tasks: dict[str, asyncio.Task] = {}
_restarts: dict[str, int] = {}
_loop: asyncio.AbstractEventLoop | None = None


def get_kernel_tasks() -> dict[str, asyncio.Task]:
    """Return all active supervised kernel asyncio tasks."""
    return _global_kernel_tasks


def _supervise(name: str, factory) -> asyncio.Task:
    try:
        from . import _watchdog
        wd = _watchdog.get_watchdog()
        if wd:
            return wd.register(name, factory)
    except Exception as exc:
        log.debug("Watchdog registration fallback for %s: %s", name, exc)

    async def runner() -> None:
        while True:
            try:
                _log_event("kernel", "subsystem_start", name=name)
                await factory()
            except asyncio.CancelledError:
                _log_event("kernel", "subsystem_cancelled", name=name)
                return
            except Exception as exc:  # noqa: BLE001
                _restarts[name] = _restarts.get(name, 0) + 1
                count = _restarts[name]
                log.warning("subsystem %s died (%s) - restart #%d", name, exc, count)
                try:
                    from . import errlog
                    errlog.log_error(f"subsystem:{name}", str(exc))
                except Exception as e_err:
                    log.warning("Errlog write failed for %s: %s", name, e_err)
                if count >= 5:
                    log.error(
                        "subsystem %s crashed %d times -- entering graceful degradation",
                        name, count,
                    )
                    try:
                        from . import errlog
                        errlog.log_error(
                            f"subsystem:{name}:degraded",
                            f"graceful degradation active after {count} crashes",
                        )
                    except Exception as deg_err:
                        log.warning("Errlog degraded note failed for %s: %s", name, deg_err)
                    bus.publish("system.degraded", {
                        "subsystem": name,
                        "crashes": count,
                        "status": "degraded",
                    })
                    await asyncio.sleep(300)
                    continue
                await asyncio.sleep(min(2 * max(1, count), 10))

    task = _loop.create_task(runner(), name=name)
    _tasks[name] = task
    _global_kernel_tasks[name] = task
    return task


async def _server_subsystem() -> None:
    import uvicorn
    from .config import settings
    from .server import app

    ucfg = uvicorn.Config(app, host=settings.host, port=settings.port,
                          log_level="warning", lifespan="off")
    server = uvicorn.Server(ucfg)
    try:
        await server.serve()
    except (OSError, SystemExit) as exc:
        log.warning("Server port %d bind delay: %s. Retrying in 2s...", settings.port, exc)
        await asyncio.sleep(2)
        raise exc


async def _voice_subsystem() -> None:
    from .voice.gateway import gateway

    gateway.start()
    while True:
        await asyncio.sleep(5)


def main(voice: bool = True) -> None:
    global _loop
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    db.migrate()
    from . import jobs, skills

    jobs.resume_running()
    tools.load_builtin_tools()
    n_skills = skills.load_all()
    if n_skills:
        log.info("re-armed %d learned skill(s)", n_skills)
    try:
        from .voice import tts as _tts
        _tts.warm_piper()
        if os.environ.get("EVO_VOICE", "1") == "1":
            from . import llm

            def _warm():
                try:
                    llm.chat([{"role": "user", "content": "ping"}], role="voice", timeout=5, max_providers=1)
                except Exception as warm_err:
                    log.debug("Voice model warm note: %s", warm_err)

            threading.Thread(target=_warm, daemon=True, name="warm-voice-model").start()
    except Exception as exc:
        log.debug("Piper initialization note: %s", exc)

    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    bus.attach_loop(_loop)

    from . import _watchdog

    wd = _watchdog.init_watchdog(_loop)
    _loop.create_task(wd.start(), name="watchdog-main")

    def _journal_callback(event_or_topic: Any, payload: dict | None = None) -> None:
        if hasattr(event_or_topic, "topic"):
            t = event_or_topic.topic
            p = event_or_topic.payload
        else:
            t = str(event_or_topic)
            p = payload or {}
        if not t.startswith("system.health"):
            db.record_event(t, p)

    bus.subscribe("*", _journal_callback)

    import atexit
    atexit.register(db.set_last_shutdown_ts)

    # Event replay for crash recovery
    last_shutdown = db.get_last_shutdown_ts()
    if last_shutdown > 0:
        replayed = db.replay_events(last_shutdown)
        if replayed:
            log.info("Replaying %d journaled event(s) since last shutdown", len(replayed))
            for ev in replayed:
                topic = ev.get("topic", "")
                if not topic.startswith("system.") and not topic.startswith("heartbeat"):
                    bus.publish(topic, ev.get("payload", {}))

    _supervise("server", _server_subsystem)

    async def _voice_v2_bootline() -> None:
        await asyncio.sleep(4)
        try:
            from .voice import webrtc_v2
            log.info(webrtc_v2.boot_line())
        except Exception as exc:
            log.debug("Voice v2 boot line note: %s", exc)

    _supervise("voicev2log", _voice_v2_bootline)

    # Communication surfaces
    tg_stop = None
    from .config import settings

    if os.environ.get("EVO_TELEGRAM", "1") == "1" and settings.telegram_token:
        from . import telegram_link

        telegram_link.ensure_bridge()
        tg_stop = threading.Event()

        async def _telegram_subsystem() -> None:
            await asyncio.to_thread(telegram_link.run_polling, tg_stop)

        _supervise("telegram", _telegram_subsystem)
        log.info("telegram link enabled")

    if os.environ.get("EVO_PUSH", "1") == "1":
        from . import push_notify
        push_notify.start_bridge()

    try:
        from . import notifications
        notifications.start_notification_bridge()
    except Exception as exc:
        log.debug("Notification bridge note: %s", exc)

    async def _reminder_tick() -> None:
        from . import reminders
        while True:
            try:
                reminders.tick(bus.publish)
            except Exception as exc:
                log.warning("Reminder tick error: %s", exc)
            await asyncio.sleep(2)

    _supervise("reminders", _reminder_tick)

    async def _job_tick() -> None:
        from . import jobs
        while True:
            try:
                jobs.promote_queued()
            except Exception as exc:
                log.warning("Job queue promote error: %s", exc)
            await asyncio.sleep(5)

    _supervise("jobtick", _job_tick)

    # Scheduled workflows & habits
    async def _workflow_tick() -> None:
        from . import workflows
        while True:
            try:
                due = workflows.due_now()
                for name in due:
                    def _run_wf(n=name):
                        r = workflows.run(n, publish_progress=lambda m: None)
                        bus.publish("notify.out", {
                            "kind": "workflow",
                            "text": (f"Scheduled workflow '{n}' "
                                     + ("finished." if r.get("ok") else "had failures."))
                        })
                        workflows.mark_ran(n)
                    await asyncio.to_thread(_run_wf)
            except Exception as exc:
                log.warning("Workflow execution tick error: %s", exc)
            await asyncio.sleep(30)

    _supervise("workflowtick", _workflow_tick)

    async def _habit_tick() -> None:
        from . import habits
        while True:
            try:
                found = await asyncio.to_thread(habits.scan)
                for p in found:
                    bus.publish("notify.out", {
                        "kind": "suggestion",
                        "text": p["detail"] + f" Say 'approve {p['id']}' to automate it."
                    })
            except Exception as exc:
                log.warning("Habit scan tick error: %s", exc)
            await asyncio.sleep(600)

    _supervise("habittick", _habit_tick)

    if os.environ.get("EVO_SELFCHECK", "1") == "1":
        minutes = max(30, int(os.environ.get("EVO_SELFCHECK_MINUTES", "180")))

        async def _selfcheck_tick() -> None:
            from . import selfcheck
            while True:
                try:
                    report = await asyncio.to_thread(selfcheck.tick)
                    for issue in report.get("issues", []):
                        bus.publish("notify.out", {
                            "kind": "selfcheck",
                            "text": f"Self-check: {issue['detail'][:180]}"
                                    + (" - dev task staged a fix, awaiting your apply."
                                       if not os.environ.get("EVO_SELF_HEAL_AUTOAPPLY", "") == "1"
                                       else " - auto-healing with revert net.")
                        })
                except Exception as exc:
                    log.warning("Self-check execution error: %s", exc)
                    bus.publish("notify.out", {"kind": "selfcheck", "text": f"Self-check error: {exc}"})
                await asyncio.sleep(minutes * 60)

        _supervise("selfchecktick", _selfcheck_tick)

    if os.environ.get("EVO_INITIATIVE", "1") == "1":
        async def _initiative_tick() -> None:
            from . import initiative_engine
            while True:
                try:
                    await asyncio.to_thread(initiative_engine.maybe_initiate, bus.publish)
                except Exception as exc:
                    log.warning("Initiative engine execution error: %s", exc)
                await asyncio.sleep(30 * 60)

        _supervise("initiativetick", _initiative_tick)

    if os.environ.get("EVO_KNOWLEDGE_WATCH", "1") == "1":
        async def _knowledge_tick() -> None:
            from . import rag
            while True:
                try:
                    await asyncio.to_thread(rag.watch_scan)
                except Exception as exc:
                    log.warning("Knowledge watch scan error: %s", exc)
                await asyncio.sleep(600)

        _supervise("knowledgetick", _knowledge_tick)

    if os.environ.get("EVO_MEMORY_TICK", "1") == "1":
        async def _memory_tick() -> None:
            from . import memory
            while True:
                try:
                    await asyncio.to_thread(memory.summarize_and_archive)
                except Exception as exc:
                    log.warning("Memory summarize and archive error: %s", exc)
                await asyncio.sleep(600)

        _supervise("memorytick", _memory_tick)

    from .awareness import run_checks as _run_awareness

    async def _awareness_loop() -> None:
        while True:
            try:
                alerts = _run_awareness(bus.publish)
                for alert in alerts:
                    bus.publish("notify.out", {"kind": "watcher", "text": alert})
            except Exception as exc:
                log.warning("Awareness loop check error: %s", exc)
            await asyncio.sleep(120)

    if os.environ.get("EVO_AWARENESS", "1") == "1":
        _supervise("awareness", _awareness_loop)

    if os.environ.get("EVO_PERCEPTION", "1") == "1":
        try:
            from . import perception
            perception.start_perception_loop(interval=5.0)
        except Exception as exc:
            log.warning("Perception loop startup error: %s", exc)

    async def _autonomy_subsystem() -> None:
        from . import autonomy
        from .jarvis_agent import get_jarvis_agent
        from .money_engine import get_money_engine
        loop_obj = autonomy.get_autonomy_loop()
        loop_obj.start()
        money_obj = get_money_engine()
        money_obj.start()
        jarvis_obj = get_jarvis_agent()
        jarvis_obj.start()
        while True:
            await asyncio.sleep(60)

    _supervise("autonomy", _autonomy_subsystem)

    if voice and os.environ.get("EVO_WAKE", "0") == "1":
        _supervise("voice", _voice_subsystem)
    elif not voice:
        log.info("voice disabled (--no-voice)")
    else:
        log.info("wake-word idle (EVO_WAKE=0). Push-to-talk + /api/tts remain active.")

    if os.environ.get("EVO_MODEL_WARMER", "1") == "1":
        warmer_state = {"n": 0}

        async def _warmer() -> None:
            while True:
                try:
                    role = "fast" if warmer_state["n"] % 2 == 0 else "primary"
                    llm.chat([{"role": "user", "content": "ping"}],
                             role=role, temperature=0, timeout=10,
                             bias=False, max_providers=1)
                    warmer_state["n"] += 1
                except Exception as exc:
                    log.debug("Model warmer ping note: %s", exc)
                await asyncio.sleep(150)

        _supervise("model-warmer", _warmer)

    async def _resource_monitor() -> None:
        """Periodic 60s health check: memory usage, thread count, disk space."""
        import shutil
        while True:
            try:
                threads = threading.active_count()
                if threads > 100:
                    log.warning("High thread count: %d threads active", threads)
                    bus.publish("system.alert", {"type": "thread_count", "count": threads})
                try:
                    usage = shutil.disk_usage(config.DATA)
                    free_gb = usage.free / (1024 ** 3)
                    if free_gb < 1.0:
                        log.warning("Low disk space: %.2f GB remaining", free_gb)
                        bus.publish("system.alert", {"type": "disk_space", "free_gb": free_gb})
                except Exception as d_exc:
                    log.warning("Disk check error: %s", d_exc)
            except Exception as r_exc:
                log.warning("Resource monitor error: %s", r_exc)
            await asyncio.sleep(60)

    _supervise("resourcemonitor", _resource_monitor)

    log.info("EVO MK2 kernel online: http://%s:%d", settings.host, settings.port)
    try:
        from .voice import webrtc_v2
        log.info(webrtc_v2.boot_line())
    except Exception as exc:
        log.debug("Voice v2 boot line note: %s", exc)

    if "--tray" in sys.argv or (os.environ.get("EVO_TRAY", "1") == "1" and "--service" not in sys.argv):
        try:
            from . import tray_icon
            tray_icon.launch_tray_in_background(on_exit=lambda: _loop.call_soon_threadsafe(_loop.stop))
        except Exception as exc:
            log.warning("Tray icon startup note: %s", exc)
    try:
        _loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if tg_stop is not None:
            tg_stop.set()
        for t in _tasks.values():
            t.cancel()
        _loop.close()


boot = main

if __name__ == "__main__":
    main()
