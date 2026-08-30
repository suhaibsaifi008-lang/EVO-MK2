import pytest
from mk2 import conversation


def test_detect_correction():
    # Correction patterns
    assert conversation.detect_correction("no, I meant tomorrow") == "tomorrow"
    assert conversation.detect_correction("actually, not that - switch to python") == "- switch to python" or "switch to python" in conversation.detect_correction("actually, not that - switch to python")
    assert conversation.detect_correction("correction: make it 5pm") == "make it 5pm"

    # Normal queries should return None
    assert conversation.detect_correction("what is the weather") is None
    assert conversation.detect_correction("show me the files") is None
