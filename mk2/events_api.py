"""SSE feed of system events for the face/console."""
import asyncio
import json

from fastapi.responses import StreamingResponse

from .bus import bus


def register(app) -> None:
    @app.get("/api/events")
    async def events():
        async def source():
            sub, q = bus.subscribe_async("**")
            try:
                yield 'data: {"type":"hello"}\n\n'
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    payload = json.dumps({"type": ev.topic,
                                          "payload": ev.payload}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
            finally:
                bus.unsubscribe(sub)

        return StreamingResponse(source(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
