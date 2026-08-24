"""Phase 7: Style controller — reads your tone, shapes the reply.

A fast-model pass classifies each incoming message (angry, stressed,
joking, terse, curious or neutral). The result becomes prompt directives
that reshape verbosity and warmth, and it feeds the feedback loop:
when you praise or push back on a reply style, that preference is stored
and re-applied in future turns.
"""
import json
import os
import threading

from . import db

_lock = threading.Lock()
_cache: dict[str, dict] = {}          # text -> classification (LRU-ish)
_state = {"last_tone": None}

TONES = ("angry", "stressed", "joking", "terse", "curious", "neutral")

_POSITIVE = ("thanks", "thank you", "nice", "perfect", "great", "good job",
             "awesome", "love it", "exactly", "well done")
_NEGATIVE = ("no,", "wrong", "useless", "stupid", "idiot", "bad answer",
             "not what i asked", "you misunderstood", "garbage")

DIRECTIVES = {
    "angry": ("The user sounds frustrated. Reply SHORTER than usual, calm "
              "and practical. Lead with the concrete fix or next step. No "
              "jokes, no filler, no apologies longer than one clause."),
    "stressed": ("The user seems stressed. Be warm and steady. Give one "
                 "reassuring line, then the smallest useful next step. "
                 "Avoid lists of more than 3 items."),
    "joking": ("The user is being playful. Match the energy with light wit, "
               "but still land the actual answer."),
    "terse": ("The user writes in short bursts. Mirror them: minimal words, "
              "zero preamble, direct answer first."),
    "curious": ("The user is exploring an idea. Add one genuinely "
                "interesting angle they didn't ask about."),
    "neutral": "",
}


def _heuristic_classify(text: str) -> dict:
    """Zero-latency tone guess: caps, punctuation, length, keywords."""
    t = (text or "").strip()
    low = t.lower()
    letters = [c for c in t if c.isalpha()]
    caps = (sum(1 for c in letters if c.isupper()) / max(1, len(letters)))
    if any(w in low for w in ("stressed", "overwhelmed", "anxious",
                              "panicking", "deadline")):
        return {"tone": "stressed"}
    if caps > 0.6 and len(letters) > 8 or t.count("!") >= 3:
        return {"tone": "angry"}
    if any(w in low for w in ("lol", "haha", "lmao", "joke", "😂", "😅")):
        return {"tone": "joking"}
    if len(t.split()) <= 3 and "?" not in t:
        return {"tone": "terse"}
    if low.startswith(("why", "how come", "i wonder", "what if")) \
            or low.endswith("?"):
        return {"tone": "curious"}
    return {"tone": "neutral"}


instant_classify = _heuristic_classify  # zero-latency path, testable directly


def classify(user_text: str) -> dict:
    """Tone classification. Default: instant local heuristics.
    Set EVO_STYLE_MODEL=1 to use the fast model instead (adds ~1-2s)."""
    text = (user_text or "").strip()
    if not text:
        return {"tone": "neutral"}
    if os.environ.get("EVO_STYLE_MODEL", "0").strip() == "1":
        with _lock:
            cached = _cache.get(text[:120])
        if cached:
            return cached
        cls = _model_classify(text)
        with _lock:
            if len(_cache) > 200:
                _cache.clear()
            _cache[text[:120]] = cls
        return cls
    return _heuristic_classify(text)


def _model_classify(text: str) -> dict:
    from . import llm

    try:
        raw = llm.chat(
            [{"role": "system",
              "content": ('Classify the user message tone. Reply ONLY JSON '
                          '{"tone":"angry|stressed|joking|terse|curious|'
                          'neutral"}. Terse = short but not upset.')},
             {"role": "user", "content": text[:500]}],
            role="fast", temperature=0.0, timeout=6)
        m = __import__("re").search(r"\{.*\}", raw, __import__("re").DOTALL)
        data = json.loads(m.group(0)) if m else {}
        tone = data.get("tone") if data.get("tone") in TONES else "neutral"
        return {"tone": tone}
    except Exception:
        return {"tone": "neutral"}


real_classify = _model_classify  # tests patch classify; keep original reachable


def directive(user_text: str) -> str:
    """Tone-specific system-prompt directive for this message."""
    cls = classify(user_text)
    with _lock:
        _state["last_tone"] = cls["tone"]
    d = DIRECTIVES.get(cls["tone"], "")
    pref = _preference_line(cls["tone"])
    return (d + ("\n" + pref if pref else "")).strip()


def _preference_line(tone: str) -> str:
    facts = {f["key"]: f["value"] for f in db.all_facts(40)}
    pos = sum(int(float(facts.get(f"feedback:{tone}:positive", "0") or 0)
                  or 0) for _ in [0])
    neg = int(float(facts.get(f"feedback:{tone}:negative", "0") or 0) or 0)
    if pos >= 2 and pos > neg * 2:
        return (f"Note: the user has responded well to your {tone}-mode "
                "replies before - keep this register.")
    if neg >= 2 and neg > pos:
        return (f"Note: the user has disliked {tone}-mode replies before - "
                "adjust toward plain and brief.")
    return ""


def note_feedback(user_text: str) -> bool:
    """If the user is reacting to the previous reply's STYLE, store it.
    Negative feedback with an instruction becomes a STANDING CORRECTION
    that is injected into every future turn. Returns True on signal."""
    text = (user_text or "").lower().strip(" .!")
    with _lock:
        tone = _state.get("last_tone")
    if not tone:
        return False
    if any(p in text for p in _POSITIVE):
        polarity = "positive"
    elif any(n in text for n in _NEGATIVE):
        polarity = "negative"
    else:
        return False
    key = f"feedback:{tone}:{polarity}"
    current = 0
    for f in db.all_facts(40):
        if f["key"] == key:
            try:
                current = int(float(f["value"]))
            except Exception:
                current = 0
    db.remember_fact(key, str(current + 1), source="feedback")
    # correction capture: a complaint that tells EVO what to do instead
    if polarity == "negative":
        import re

        triggers = ("instead", "should", "don't", "do not", "always",
                    "never", "from now on", "next time")
        for sent in re.split(r"(?<=[.!?])\s+", user_text):
            low = sent.lower()
            if any(t in low for t in triggers) and len(sent.strip()) > 12:
                existing = [f["value"] for f in db.all_facts(40)
                            if f["key"].startswith("rule:")]
                rule = sent.strip()[:200]
                if rule in existing:
                    return True
                db.remember_fact(f"rule:{len(existing)+1}-{tone}",
                                 rule, source="correction")
                return True
    return True


def last_tone() -> str | None:
    with _lock:
        return _state.get("last_tone")
