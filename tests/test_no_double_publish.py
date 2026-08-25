"""Console turns must NOT be re-broadcast on the bus (they are rendered
locally by the UI) — that double-publish made every spoken/typed turn
appear twice in the chat log. Server-side surfaces (voice) still publish."""
import pytest

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dup.db")
    db.migrate()


def _turn_events(surface):
    from mk2 import brain
    from mk2.bus import bus

    seen = []
    sub = bus.subscribe("convo.turn", callback=lambda ev: seen.append(ev))
    try:
        brain.handle_turn("what time is it", surface=surface)
    finally:
        bus.unsubscribe(sub)
    return seen


def test_console_turn_not_published():
    assert _turn_events("console") == []


def test_voice_surface_still_published():
    events = _turn_events("voice")
    assert len(events) == 1
    assert events[0].payload["text"] == "what time is it"
