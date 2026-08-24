"""YouTube summarization, voice selection, instant style heuristics."""
import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()
    from mk2 import persona_loader

    monkeypatch.setattr(persona_loader, "PERSONA_PATH", tmp_path / "p.md")


class TestVideoId:
    def test_all_url_shapes(self):
        from mk2.youtube_tools import _video_id

        vid = "dQw4w9WgXcQ"
        for s in (f"https://www.youtube.com/watch?v={vid}&t=30s",
                  f"https://youtu.be/{vid}", f"https://youtube.com/shorts/{vid}",
                  f"https://www.youtube.com/embed/{vid}", vid):
            assert _video_id(s) == vid
        assert _video_id("https://vimeo.com/12345") is None


class TestYoutubeSummarize:
    def test_summarize_with_mocked_transcript(self, monkeypatch, tmp_path):
        from mk2 import youtube_tools as yt

        monkeypatch.setattr(yt, "_fetch_transcript",
                            lambda vid: ("This video explains solid state "
                                         "batteries. Key point: energy "
                                         "density doubles. ") * 5)
        seen = {}
        import mk2.llm as llm

        def fake_chat(msgs, **k):
            seen["sys"] = msgs[0]["content"]
            return "SUMMARY: batteries explained well."
        monkeypatch.setattr(llm, "chat", fake_chat)

        r = tools.call("youtube_summarize",
                       {"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r["ok"] is True
        assert "batteries" in r["speech"].lower() or "summary" in r["speech"].lower()

    def test_save_to_vault(self, monkeypatch, tmp_path):
        from mk2 import youtube_tools as yt

        monkeypatch.setattr(yt, "_fetch_transcript", lambda vid: "content here " * 20)
        monkeypatch.setattr("mk2.llm.chat", lambda *a, **k: "the summary")
        monkeypatch.setattr("mk2.vault.VAULT_DIR", tmp_path / "vault")
        r = tools.call("youtube_summarize",
                       {"url": "https://youtu.be/dQw4w9WgXcQ", "save": True})
        assert "saved" in r["speech"].lower()

    def test_no_captions_graceful(self, monkeypatch):
        from mk2 import youtube_tools as yt

        def boom(vid):
            raise RuntimeError("No transcripts found")
        monkeypatch.setattr(yt, "_fetch_transcript", boom)
        r = tools.call("youtube_summarize", {"url": "https://youtu.be/dQw4w9WgXcQ"})
        assert r["ok"] is False and "captions" in r["speech"].lower()

    def test_bad_link_rejected(self):
        r = tools.call("youtube_summarize", {"url": "https://vimeo.com/99"})
        assert r["ok"] is False


class TestVoiceSelection:
    def test_list_voices(self):
        r = tools.call("tts_voices", {})
        assert r["ok"] is True
        assert "neural_options" in r["data"]

    def test_set_neural_voice(self, monkeypatch):
        r = tools.call("tts_set_voice", {"voice": "en-IN-NeerjaNeural"})
        assert r["ok"] is True
        import os

        try:
            assert os.environ.get("EVO_TTS_VOICE") == "en-IN-NeerjaNeural"
        finally:
            os.environ.pop("EVO_TTS_VOICE", None)   # no leakage

    def test_edge_voice_env_used(self, monkeypatch):
        from mk2.voice import tts

        monkeypatch.setenv("EVO_TTS_VOICE", "en-IN-PrabhatNeural")
        assert tts._edge_voice() == "en-IN-PrabhatNeural"
        monkeypatch.delenv("EVO_TTS_VOICE")
        assert tts._edge_voice() == "en-US-AndrewMultilingualNeural"

    def test_unknown_windows_voice_rejected(self, monkeypatch):
        monkeypatch.setattr(_tts_mod(), "list_sapi_voices", lambda: ["Microsoft Zira"])
        r = tools.call("tts_set_voice", {"voice": "Nonexistent Voice X"})
        assert r["ok"] is False

    def test_windows_voice_substring(self, monkeypatch):
        monkeypatch.setattr(_tts_mod(), "list_sapi_voices",
                            lambda: ["Microsoft Zira Desktop", "David"])
        r = tools.call("tts_set_voice", {"voice": "zira"})
        assert r["ok"] is True
        import os

        assert os.environ.get("EVO_SAPI_VOICE") == "Microsoft Zira Desktop"


def _tts_mod():
    from mk2.voice import tts

    return tts


class TestInstantStyle:
    def test_caps_means_angry(self, monkeypatch):
        from mk2 import style_controller as sc

        monkeypatch.delenv("EVO_STYLE_MODEL", raising=False)
        c = sc.instant_classify("THIS IS BROKEN AGAIN AND AGAIN!!!")
        assert c["tone"] in ("angry",)

    def test_short_is_terse(self, monkeypatch):
        from mk2 import style_controller as sc

        monkeypatch.delenv("EVO_STYLE_MODEL", raising=False)
        assert sc.instant_classify("k")["tone"] == "terse"

    def test_no_network_call_by_default(self, monkeypatch):
        from mk2 import style_controller as sc

        called = []
        monkeypatch.setattr("mk2.llm.chat",
                            lambda *a, **k: called.append(1) or "{}")
        sc.instant_classify("a normal sentence about laptops under fifty thousand")
        assert called == []          # heuristic path: zero model hops
