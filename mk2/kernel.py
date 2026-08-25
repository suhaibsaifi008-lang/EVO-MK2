"""Kernel: owns the loop, supervises subsystems, restarts on death."""
import asyncio
import logging
import os
import threading

from . import db, llm, tools
from .bus import bus

log = logging.getLogger("mk2.kernel")

_tasks: dict[str, asyncio.Task] = {}
_restarts: dict[str, int] = {}
_loop: asyncio.AbstractEventLoop | None = None


def _supervise(name: str, factory) -> asyncio.Task:
    async def runner() -> None:
        while True:
            try:
                await factory()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                _restarts[name] = _restarts.get(name, 0) + 1
                log.warning("subsystem %s died (%s) - restart #%d",
                            name, exc, _restarts[name])
                try:
                    from . import errlog

                    errlog.log_error(f"subsystem:{name}", str(exc))
                except Exception:
                    pass
                await asyncio.sleep(min(2 * max(1, _restarts[name]), 10))

    task = _loop.create_task(runner(), name=name)
    _tasks[name] = task
    return task


async def _server_subsystem() -> None:
    import uvicorn

    from .config import settings
    from .server import app

    ucfg = uvicorn.Config(app, host=settings.host, port=settings.port,
                          log_level="warning", lifespan="off")
    server = uvicorn.Server(ucfg)
    await server.serve()


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
    try:  # pre-load the local Piper JARVIS voice (first reply pays no load tax)
        from .voice import tts as _tts

        _tts.warm_piper()
    except Exception:
        pass
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    bus.attach_loop(_loop)

    _supervise("server", _server_subsystem)

    async def _voice_v2_bootline() -> None:
        await asyncio.sleep(4)      # let the server app finish registering
        try:
            from .voice import webrtc_v2

            log.info(webrtc_v2.boot_line())
        except Exception:
            pass

    _supervise("voicev2log", _voice_v2_bootline)

    # Phase 2: communication surfaces -------------------------------------
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

    async def _reminder_tick() -> None:
        from . import reminders

        while True:
            reminders.tick(bus.publish)
            await asyncio.sleep(2)

    _supervise("reminders", _reminder_tick)

    async def _job_tick() -> None:
        from . import jobs

        while True:
            try:
                jobs.promote_queued()
            except Exception:
                pass
            await asyncio.sleep(5)

    _supervise("jobtick", _job_tick)

    # Phase 5: scheduled workflows + habit proposals ----------------------
    async def _workflow_tick() -> None:
        from . import workflows

        while True:
            try:
                due = workflows.due_now()
                for name in due:
                    def _run_wf(n=name):
                        r = workflows.run(
                            n, publish_progress=lambda m: None)
                        bus.publish("notify.out", {
                            "kind": "workflow",
                            "text": (f"Scheduled workflow '{n}' "
                                     + ("finished." if r.get("ok")
                                        else "had failures."))})
                        workflows.mark_ran(n)
                    await asyncio.to_thread(_run_wf)
            except Exception:
                pass
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
                        "text": p["detail"] +
                                f" Say 'approve {p['id']}' to automate it."})
            except Exception:
                pass
            await asyncio.sleep(600)

    _supervise("habittick", _habit_tick)

    # Phase 5.5: EVO watches its own health ------------------------------
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
                                    + (" - dev task staged a fix, awaiting "
                                       "your apply." if not os.environ.get(
                                           "EVO_SELF_HEAL_AUTOAPPLY", "") == "1"
                                       else " - auto-healing with revert net.")})
                except Exception:
                    pass
                await asyncio.sleep(minutes * 60)

        _supervise("selfchecktick", _selfcheck_tick)

    # Phase 7: initiative engine ------------------------------------------
    if os.environ.get("EVO_INITIATIVE", "1") == "1":

        async def _initiative_tick() -> None:
            from . import initiative_engine

            while True:
                try:
                    await asyncio.to_thread(initiative_engine.maybe_initiate,
                                            bus.publish)
                except Exception:
                    pass
                await asyncio.sleep(30 * 60)

        _supervise("initiativetick", _initiative_tick)

    # Phase 7.6: knowledge watcher ----------------------------------------
    if os.environ.get("EVO_KNOWLEDGE_WATCH", "1") == "1":

        async def _knowledge_tick() -> None:
            from . import rag

            while True:
                try:
                    await asyncio.to_thread(rag.watch_scan)
                except Exception:
                    pass
                await asyncio.sleep(600)

        _supervise("knowledgetick", _knowledge_tick)

    # Phase 4: periodic conversation compression -> semantic episodes
    if os.environ.get("EVO_MEMORY_TICK", "1") == "1":

        async def _memory_tick() -> None:
            from . import memory

            while True:
                try:
                    await asyncio.to_thread(memory.summarize_and_archive)
                except Exception:
                    pass
                await asyncio.sleep(600)

        _supervise("memorytick", _memory_tick)

    from .awareness import run_checks as _run_awareness

    async def _awareness_loop() -> None:
        while True:
            try:
                alerts = _run_awareness(bus.publish)
                for alert in alerts:
                    bus.publish("notify.out", {"kind": "watcher", "text": alert})
            except Exception:
                pass
            await asyncio.sleep(120)

    if os.environ.get("EVO_AWARENESS", "1") == "1":
        _supervise("awareness", _awareness_loop)

    if voice and os.environ.get("EVO_WAKE", "0") == "1":
        _supervise("voice", _voice_subsystem)
    elif not voice:
        log.info("voice disabled (--no-voice)")
    else:
        log.info("wake-word idle (EVO_WAKE=0). Push-to-talk + /api/tts remain active.")

    # Keep the local model resident: otherwise Ollama unloads after ~5 min
    # idle and EVERY reply pays a 15s+ reload tax.
    if os.environ.get("EVO_MODEL_WARMER", "1") == "1":

        async def _warmer() -> None:
            while True:
                try:
                    llm.chat([{"role": "user", "content": "ping"}],
                             role="fast", temperature=0, timeout=10,
                             bias=False, max_providers=1)
                except Exception:
                    pass
                await asyncio.sleep(240)

        _supervise("model-warmer", _warmer)

    log.info("EVO MK2 kernel online: http://%s:%d", settings.host, settings.port)
    try:
        from .voice import webrtc_v2

        log.info(webrtc_v2.boot_line())
    except Exception:
        pass
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
