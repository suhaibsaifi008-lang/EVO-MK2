import pytest
from mk2 import db, persona_loader


def test_anecdote_crud():
    db.migrate()
    anec_id = db.save_anecdote("repulsor_misfire", "User accidentally triggered repulsor test on the lab wall", "funny")
    assert anec_id > 0

    rows = db.anecdotes_by_emotion("funny", limit=5)
    assert any(r["name"] == "repulsor_misfire" for r in rows)

    humor_ctx = persona_loader.get_humor_context()
    assert "repulsor_misfire" in humor_ctx
