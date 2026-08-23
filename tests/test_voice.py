from mk2.voice import grammar_rescue as gr
from mk2.voice import wake


class TestWake:
    def test_exact(self):
        assert wake.match_wake("wake up evo") == ""

    def test_ride_along_command(self):
        rest = wake.match_wake("wake up evo open youtube")
        assert rest is not None and "youtube" in rest

    def test_misheard_still_triggers(self):
        assert wake.match_wake("woke up evo") is not None

    def test_noise_ignored(self):
        assert wake.match_wake("play some music please") is None

    def test_exit_detection(self):
        assert wake.is_exit("okay goodbye then")
        assert not wake.is_exit("open chrome")


class TestGrammarTrust:
    def test_trusts_verb_commands(self):
        assert gr.trust_grammar("open youtube", "open u tube") is True

    def test_never_trusts_on_long_conversation(self):
        long_chat = ("what do you think about the political situation "
                     "in the region right now honestly")
        assert gr.trust_grammar("open wikipedia", long_chat) is False

    def test_bare_word_not_enough(self):
        assert gr.trust_grammar("wikipedia", "wake up ever") is False

    def test_phrase_list_clean_and_populated(self):
        phrases = gr.grammar_phrases()
        assert len(phrases) > 60
        assert not any("{" in p for p in phrases)
        assert any(p == "open youtube" for p in phrases)
