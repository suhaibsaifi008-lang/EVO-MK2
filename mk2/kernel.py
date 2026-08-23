"""Kernel: owns the loop, supervises subsystems, restarts on death."""
import asyncio
import logging

from . import db, tools
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
    tools.load_builtin_tools()
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    bus.attach_loop(_loop)

    _supervise("server", _server_subsystem)

    async def _reminder_tick() -> None:
        from . import reminders

        while True:
            reminders.tick(bus.publish)
            await asyncio.sleep(2)

    _supervise("reminders", _reminder_tick)

    import os

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

    from .config import settings

    log.info("EVO MK2 kernel online: http://%s:%d", settings.host, settings.port)
    try:
        _loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for t in _tasks.values():
            t.cancel()
        _loop.close()
