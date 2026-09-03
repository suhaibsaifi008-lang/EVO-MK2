"""Predictive user pattern learning and anticipation engine for EVO MK2.

Extracts recurring temporal and behavioural patterns (coffee times, focus blocks,
stand-up nudges, meeting preps) and anticipates user needs with Bayesian confidence.
"""
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import config

log = logging.getLogger("mk2.patterns")

PATTERNS_FILE = config.DATA / "patterns.json"


def _load_patterns() -> list[dict]:
    if PATTERNS_FILE.exists():
        try:
            return json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_patterns(patterns: list[dict]) -> None:
    try:
        PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PATTERNS_FILE.write_text(json.dumps(patterns[-100:], indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("Could not save patterns: %s", exc)


def learn_pattern(pattern_type: str, time_str: str, day_of_week: int,
                  action_hint: str, confidence: float = 0.5) -> dict:
    """Record or update a recurring behavioural pattern."""
    patterns = _load_patterns()
    # Match existing pattern with same type and similar time (+- 30 mins)
    for p in patterns:
        if p.get("type") == pattern_type and p.get("day_of_week") in (day_of_week, -1):
            p_time = p.get("time", "")
            if p_time and ":" in p_time and ":" in time_str:
                try:
                    ph, pm = map(int, p_time.split(":")[:2])
                    th, tm = map(int, time_str.split(":")[:2])
                    diff = abs((ph * 60 + pm) - (th * 60 + tm))
                    if min(diff, 1440 - diff) <= 30:
                        # Update confidence and count
                        p["occurrences"] = p.get("occurrences", 1) + 1
                        p["confidence"] = min(0.95, p.get("confidence", 0.5) + 0.1)
                        p["last_updated"] = time.time()
                        p["action_hint"] = action_hint or p.get("action_hint", "")
                        _save_patterns(patterns)
                        return p
                except Exception:
                    pass

    new_pattern = {
        "id": f"pat_{int(time.time())}_{len(patterns) + 1}",
        "type": pattern_type,
        "time": time_str,
        "day_of_week": day_of_week,  # 0-6 or -1 for every day
        "action_hint": action_hint,
        "confidence": min(0.95, max(0.2, confidence)),
        "occurrences": 1,
        "last_triggered": 0.0,
        "last_updated": time.time(),
        "success_count": 0,
        "rejection_count": 0,
    }
    patterns.append(new_pattern)
    _save_patterns(patterns)
    log.info("Learned new user pattern: %s at %s (confidence: %.2f)", pattern_type, time_str, confidence)
    return new_pattern


def update_pattern_feedback(pattern_id: str, positive: bool) -> None:
    """Strengthen or weaken a pattern based on user feedback."""
    patterns = _load_patterns()
    for p in patterns:
        if p.get("id") == pattern_id:
            if positive:
                p["success_count"] = p.get("success_count", 0) + 1
                p["confidence"] = min(0.99, p.get("confidence", 0.5) + 0.15)
                log.info("Pattern %s strengthened (confidence: %.2f)", pattern_id, p["confidence"])
            else:
                p["rejection_count"] = p.get("rejection_count", 0) + 1
                p["confidence"] = max(0.1, p.get("confidence", 0.5) - 0.20)
                log.info("Pattern %s weakened (confidence: %.2f)", pattern_id, p["confidence"])
            p["last_updated"] = time.time()
            break
    _save_patterns(patterns)


def predict_upcoming_patterns(lookahead_minutes: int = 25) -> list[dict]:
    """Find patterns predicted to occur within the lookahead window with confidence >= 0.70."""
    now = datetime.now()
    cur_day = now.weekday()
    cur_mins = now.hour * 60 + now.minute

    patterns = _load_patterns()
    matches = []
    for p in patterns:
        if p.get("confidence", 0) < 0.70:
            continue
        p_day = p.get("day_of_week", -1)
        if p_day != -1 and p_day != cur_day:
            continue

        p_time = p.get("time", "")
        if not p_time or ":" not in p_time:
            continue
        try:
            h, m = map(int, p_time.split(":"))
            pat_mins = h * 60 + m
        except ValueError:
            continue

        diff = (pat_mins - cur_mins) % 1440
        # Upcoming in 0..lookahead_minutes
        if 0 <= diff <= lookahead_minutes:
            # Don't re-trigger if triggered within last 6 hours
            if time.time() - p.get("last_triggered", 0.0) > 21600:
                p["minutes_until"] = diff
                matches.append(p)

    return matches


def mark_pattern_triggered(pattern_id: str) -> None:
    patterns = _load_patterns()
    for p in patterns:
        if p.get("id") == pattern_id:
            p["last_triggered"] = time.time()
            break
    _save_patterns(patterns)
