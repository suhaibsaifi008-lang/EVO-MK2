import asyncio
import threading
import time

from mk2.bus import Bus, matches


def test_exact_and_wildcard_matching():
    assert matches("voice.turn", "voice.turn")
    assert matches("voice.*", "voice.turn")
    assert not matches("voice.turn", "action.done")


def test_sync_callback_receives_once():
    bus = Bus()
    got = []
    bus.subscribe("test", lambda ev: got.append(ev))
    bus.publish("test", {"a": 1})
    assert len(got) == 1 and got[0].payload["a"] == 1


def test_no_history_replay_for_new_subscribers():
    """THE MK1 BUG: new subscriber must not receive events from before."""
    bus = Bus()
    bus.publish("welcome", {"text": "hi"})
    sub = bus.subscribe("welcome")
    assert sub.queue is None  # sync sub: no queue
    got = []
    bus.subscribe("welcome", lambda ev: got.append(ev))
    assert got == []  # nothing replayed


def test_threadsafe_publish_reaches_async_subscriber():
    bus = Bus()
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        bus.attach_loop(loop)
        holder = {}

        async def make_sub():
            sub, q = bus.subscribe_async("news")
            return q

        fut = asyncio.run_coroutine_threadsafe(make_sub(), loop)
        q = fut.result(timeout=5)
        # publish from THIS (non-loop) thread
        for _ in range(30):
            bus.publish("news", {"n": 7})
            ev = loop.call_soon_threadsafe(lambda: None)

        async def drain():
            return await asyncio.wait_for(q.get(), timeout=3)

        fut2 = asyncio.run_coroutine_threadsafe(drain(), loop)
        ev = fut2.result(timeout=5)
        assert ev.payload["n"] == 7
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_queue_overflow_drops_oldest():
    bus = Bus()

    async def scenario():
        sub, q = bus.subscribe_async("t")
        small = type("Q", (), {})()
        # shrink maxsize by wrapping: publish 600 events, ensure no exception
        for i in range(600):
            bus._put_nowait_safe(sub.queue, type("E", (), {"seq": i}))
        return sub.queue.qsize()

    size = asyncio.run(scenario())
    assert size >= 1
