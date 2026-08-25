"""Fast-lane voice-command parsing: STT artifacts, compound splits,
natural-language play queries, sentence-vs-appname guards."""
import pytest

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "fl.db")
    db.migrate()


@pytest.fixture()
def no_browser(monkeypatch):
    """Don't actually open tabs during tests."""
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    return opened


class TestCompoundSttArtifacts:
    def test_an_as_and_splits_voice_command(self):
        from mk2.fastlane import _split_compound

        parts = _split_compound(
            "open youtube an play the best video of minecraft")
        assert parts == ["open youtube",
                         "play the best video of minecraft"]

    def test_open_an_app_never_splits(self):
        from mk2.fastlane import _split_compound

        assert _split_compound("open an app") == ["open an app"]

    def test_real_user_sentence(self, no_browser):
        from mk2.fastlane import fast_command

        r = fast_command(
            "OPEN YOUTUBE AN PLAY THE BEST VIDEO OF MINECRAFT "
            "YOU CAN FIND FOR A NEW PLAYER", surface="test")
        assert r is not None
        assert "minecraft" in r.lower()
        # youtube results page opened with a CLEAN query
        urls = [u for u in no_browser if "youtube.com" in u]
        assert any("search_query=minecraft" in u for u in urls)
        assert all(
            "you+can+find" not in u and
            "best+video" not in u for u in urls)


class TestPlayQueryCleanup:
    def test_strips_best_video_padding(self, no_browser):
        from mk2.fastlane import fast_command

        fast_command("play the best video of lofi beats", surface="test")
        assert any("search_query=lofi+beats" in u for u in no_browser)

    def test_plain_query_untouched(self, no_browser):
        from mk2.fastlane import fast_command

        fast_command("play despacito on youtube", surface="test")
        assert any("search_query=despacito" in u for u in no_browser)


class TestOpenGuard:
    def test_long_sentence_not_treated_as_app(self, monkeypatch):
        from mk2 import tools
        from mk2.fastlane import fast_command

        called = {"n": 0}

        def fake_call(name, args=None):
            called["n"] += 1
            return {"ok": False, "speech": "no", "data": {}}

        monkeypatch.setattr(tools, "call", fake_call)
        # unsplit-able long tail (second clause lacks action verb start)
        r = fast_command(
            "open youtube maybe something else entirely here now",
            surface="test")
        assert r is None                      # -> brain handles it
        assert called["n"] == 0               # open_app never ran

    def test_short_app_names_still_work(self, monkeypatch):
        from mk2 import tools
        from mk2.fastlane import fast_command

        def fake_call(name, args=None):
            assert name == "open_app"
            assert args["target"] == "spotify"
            return {"ok": True, "speech": "Opening Spotify.", "data": {}}

        monkeypatch.setattr(tools, "call", fake_call)
        assert fast_command("open spotify") == "Opening Spotify."
