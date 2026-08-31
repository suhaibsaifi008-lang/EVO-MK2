"""Event bus — the nervous system.

Contract (fixes MK1's announcement-replay bug class):
  - Topics are plain strings ("voice.turn", "action.done", "system.*").
  - Subscriptions never replay history: you receive what happens after you
    subscribed, exactly once.
  - sync subscribers run on the PUBLISHER's thread (keep handlers fast);
    async subscribers get an asyncio.Queue fed thread-safely via call_soon.
"""
import asyncio
import itertools
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("mk2.bus")


def _log_event(subsystem: str, event: str, **kwargs):
    log.info("[%s] %s %s", subsystem, event, " ".join(f"{k}={v}" for k, v in kwargs.items()))


_time = __import__("time").time


@dataclass
class Event:
    topic: str
    payload: dict = field(default_factory=dict)
    ts: float = 0.0
    seq: int = 0


def matches(pattern: str, topic: str) -> bool:
    if pattern in ("**", "*"):
        return True
    if pattern.endswith(".*"):
        return topic.startswith(pattern[:-1])
    return pattern == topic


class Subscription:
    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.callbacks: list[Callable[[Event], None]] = []
        self.queue: asyncio.Queue | None = None
        self.active = True

    def close(self) -> None:
        self.active = False


class Bus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    # kernel attaches its running loop so cross-thread publishes are safe
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, pattern: str, callback: Callable[[Event], None] | None = None) -> Subscription:
        sub = Subscription(pattern)
        if callback:
            sub.callbacks.append(callback)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    def subscribe_async(self, pattern: str) -> tuple[Subscription, asyncio.Queue]:
        """Must be called ON the kernel loop."""
        sub = Subscription(pattern)
        sub.queue = asyncio.Queue(maxsize=512)
        with self._lock:
            self._subs.append(sub)
        return sub, sub.queue

    def publish(self, topic: str, payload: dict | None = None) -> Event:
        ev = Event(topic=topic, payload=dict(payload or {}), ts=_time(), seq=next(self._seq))
        with self._lock:
            subs = [s for s in self._subs if s.active and matches(s.pattern, topic)]
        for sub in subs:
            for cb in sub.callbacks:
                try:
                    t0 = _time()
                    cb(ev)
                    elapsed = _time() - t0
                    if elapsed > 2.0:
                        log.warning("Slow bus callback %s on %s took %.1fs", cb, topic, elapsed)
                except Exception as exc:
                    log.warning("Bus subscriber %s failed on topic %s: %s", cb, topic, exc)
            q = sub.queue
            if q is not None:
                self._offer(q, ev)
        return ev

    def _offer(self, q: asyncio.Queue, ev: Event) -> None:
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            self._put_nowait_safe(q, ev)
        elif self._loop is not None:
            self._loop.call_soon_threadsafe(self._put_nowait_safe, q, ev)
        else:
            self._put_nowait_safe(q, ev)

    @staticmethod
    def _put_nowait_safe(q: asyncio.Queue, ev: Event) -> None:
        if q.full():
            try:
                q.get_nowait()  # drop oldest: live streams beat history
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(ev)


bus = Bus()


def publish(topic: str, payload: dict | None = None) -> Event:
    return bus.publish(topic, payload)


def subscribe(pattern: str, callback: Callable[[Event], None] | None = None) -> Subscription:
    return bus.subscribe(pattern, callback)

