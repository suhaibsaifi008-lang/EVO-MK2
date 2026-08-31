"""Phase 1: Proactive Awareness — watchers + arbiter + briefing + relationship tracking.

EVO monitors your world and tells you what matters before you ask.
Upgraded: tracks user interaction patterns, emotional states, relationship depth.
"""
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from . import db
from .bus import bus

log = logging.getLogger("mk2.awareness")

_lock = __import__("threading").Lock()
_page_hashes: dict[str, str] = {}
_last_alerts: dict[str, float] = {}

# --- Relationship tracking state ---
_relationship_depth = {
	"interaction_frequency": 0,
	"preferred_topics": [],
	"emotional_states": [],
	"depth_score": 0,
	"shared_history_count": 0,
}
_emotional_states: list[dict] = []
_topic_engagement: dict[str, int] = {}
_user_preferences: dict[str, str] = {}
_interaction_log: deque = deque(maxlen=200)
_personality_facts_cache: dict = {}


def _ps(script: str, timeout: int = 15) -> str:
	import subprocess

	r = subprocess.run(
		["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
		capture_output=True, text=True, timeout=timeout,
		creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
	)
	return (r.stdout or "").strip()


def get_battery() -> tuple[int | None, bool]:
	try:
		out = _ps("(Get-WmiObject Win32_Battery).EstimatedChargeRemaining")
		pct = int(float(out))
		return pct, False
	except Exception:
		return None, False


def should_notify(dedup_key: str, min_gap_s: int = 300) -> bool:
	"""True if we haven't sent this exact alert recently."""
	with _lock:
		last = _last_alerts.get(dedup_key, 0)
		if time.time() - last < min_gap_s:
			return False
		_last_alerts[dedup_key] = time.time()
	return True


# --- Emotion detection ---
_EMOTION_PATTERNS = {
	"happy": re.compile(r"\b(happy|excited|great|wonderful|amazing|awesome|fantastic|"
						r"love it|yay|woohoo|celebrating|thrilled)\b", re.I),
	"stressed": re.compile(r"\b(stressed|overwhelmed|busy|too much|deadline|panic|"
						 r"anxious|worried|swamped|exhausted)\b", re.I),
	"sad": re.compile(r"\b(sad|disappointed|frustrated|upset|down|depressed|unhappy|"
					 r"lonely|miss|grief|hurt)\b", re.I),
	"grateful": re.compile(r"\b(thankful|grateful|appreciate|blessed|thanks|thank you)\b", re.I),
	"angry": re.compile(r"\b(angry|furious|annoyed|frustrated|pissed|mad|hate|ridiculous)\b", re.I),
	"curious": re.compile(r"\b(curious|wondering|interesting|fascinating|tell me more|"
						 r"how does|what if|why does)\b", re.I),
}

_TOPIC_KEYWORDS = {
	"work": re.compile(r"\b(work|job|office|meeting|deadline|project|boss|coworker|"
					 r"career|salary|promotion|tasks)\b", re.I),
	"health": re.compile(r"\b(health|doctor|exercise|gym|sleep|diet|medicine|sick|"
						r"workout|running|walking|mental health)\b", re.I),
	"tech": re.compile(r"\b(computer|code|program|software|app|website|tech|gadget|"
					 r"phone|laptop|AI|data|algorithm)\b", re.I),
	"relationships": re.compile(r"\b(friend|family|partner|date|relationship|love|"
								r"marriage|dating|social|hang out)\b", re.I),
	"entertainment": re.compile(r"\b(movie|show|music|game|book|read|watch|listen|"
								r"play|fun|hobby|entertainment)\b", re.I),
	"finance": re.compile(r"\b(money|budget|save|invest|spend|price|cost|expensive|"
						 r"cheap|salary|bank)\b", re.I),
	"learning": re.compile(r"\b(learn|study|course|tutorial|practice|skill|teach|"
						 r"education|knowledge|book|read)\b", re.I),
	"home": re.compile(r"\b(home|house|apartment|room|kitchen|garden|clean|"
					 r"furniture|decoration|repair)\b", re.I),
}


def _detect_emotion(text: str) -> str | None:
	for emotion, pattern in _EMOTION_PATTERNS.items():
		if pattern.search(text):
			return emotion
	return None


def _detect_topics(text: str) -> list[str]:
	detected = []
	for topic, pattern in _TOPIC_KEYWORDS.items():
		if pattern.search(text):
			detected.append(topic)
	return detected


def track_user_pattern(user_text: str, reply: str = "") -> list[str]:
	"""Track user interaction patterns, emotions, and relationship depth.
	Returns list of insights generated from this interaction."""
	insights = []
	if not user_text.strip():
		return insights
	with _lock:
		_relationship_depth["interaction_frequency"] += 1
		_interaction_log.append({
			"text": user_text[:200],
			"ts": datetime.now().isoformat(),
			"emotion": _detect_emotion(user_text),
			"topics": _detect_topics(user_text),
		})
	emotion = _detect_emotion(user_text)
	if emotion:
		with _lock:
			_emotional_states.append({
				"emotion": emotion,
				"text": user_text[:100],
				"ts": datetime.now().isoformat(),
			})
			_emotional_states[:] = _emotional_states[-50:]
			if emotion not in _relationship_depth["emotional_states"]:
				_relationship_depth["emotional_states"].append(emotion)
			_relationship_depth["emotional_states"][:] = (
				list(dict.fromkeys(_relationship_depth.get("emotional_states", [])))[-20:]
			)
		insights.append(f"Detected emotion: {emotion}")
	topics = _detect_topics(user_text)
	if topics:
		with _lock:
			for t in topics:
				_topic_engagement[t] = _topic_engagement.get(t, 0) + 1
				if t not in _relationship_depth["preferred_topics"]:
					_relationship_depth["preferred_topics"].append(t)
		top_str = ", ".join(topics)
		insights.append(f"Topics: {top_str}")
	# Update relationship depth score
	freq = _relationship_depth.get("interaction_frequency", 0)
	shared = _relationship_depth.get("shared_history_count", 0)
	emotions_count = len(_relationship_depth.get("emotional_states", []))
	topics_count = len(_relationship_depth.get("preferred_topics", []))
	score = min(100, int(
		(freq * 1.5)
		+ (shared * 3)
		+ (emotions_count * 2)
		+ (topics_count * 1)
		+ 5
	))
	_relationship_depth["depth_score"] = score
	# Store preferences from explicit statements
	pref_patterns = [
		(r"I (?:really )?(?:like|love|enjoy|prefer) (.+?)(?:\.|!|,|$)", "like"),
		(r"I (?:hate|dislike|don't like|can't stand) (.+?)(?:\.|!|,|$)", "dislike"),
		(r"My favorite (.+?) is (.+?)(?:\.|!|,|$)", "favorite"),
		(r"I always (.+?)(?:\.|!|,|$)", "habit"),
		(r"I never (.+?)(?:\.|!|,|$)", "avoid"),
	]
	for pat, ptype in pref_patterns:
		m = re.search(pat, user_text, re.I)
		if m:
			key = m.group(1).strip()[:50]
			val = m.group(2).strip()[:100] if m.lastindex >= 2 else ""
			if key:
				_user_preferences[key] = f"{ptype}: {val}"
				insights.append(f"Preference: {key} = {ptype}")
	return insights


def get_relationship_metrics() -> dict:
	"""Return relationship depth metrics for display."""
	with _lock:
		return dict(_relationship_depth)


def get_relationship_depth() -> int:
	"""Return relationship depth score (0-100)."""
	with _lock:
		score = _relationship_depth.get("depth_score", 0)
		if not score:
			interactions = _relationship_depth.get("interaction_frequency", 0)
			shared = get_shared_history_count()
			score = min(100, int(interactions * 2 + shared * 5))
			_relationship_depth["depth_score"] = score
		return int(score)


def get_user_meta() -> dict:
	"""Return comprehensive user metadata including relationship depth score."""
	return {
		"relationship_depth_score": get_relationship_depth(),
		"preferences": get_user_preferences(),
		"preferred_topics": get_preferred_topics(),
		"emotional_states": get_emotional_states(),
		"shared_history_count": get_shared_history_count(),
	}


def get_situational_formality(hour: int | None = None) -> str:
	"""Calibrate formality based on time of day and work context."""
	if hour is None:
		hour = datetime.now().hour
	if 9 <= hour <= 17:
		return "focused-work"
	if 22 <= hour or hour <= 5:
		return "late-night"
	return "conversational"


def get_emotional_states() -> list[dict]:
	"""Return recent emotional states."""
	return list(_emotional_states[-10:])


def get_user_preferences() -> dict[str, str]:
	"""Return tracked user preferences."""
	return dict(_user_preferences)


def get_preferred_topics() -> list[str]:
	"""Return topics the user engages with most."""
	with _lock:
		sorted_topics = sorted(_topic_engagement.items(), key=lambda x: -x[1])
		return [t for t, _ in sorted_topics[:10]]


def get_shared_history_count() -> int:
	"""Count how many personality facts we've stored about the user."""
	try:
		facts = _get_personality_facts(50)
		return len(facts)
	except Exception:
		return 0


def _get_personality_facts(limit: int = 50) -> dict:
	"""Pull personality-relevant facts from the database."""
	try:
		return {f["key"]: f["value"] for f in db.all_facts(limit) if f.get("source") == "personality"}
	except Exception:
		return {}


def get_recent_interactions(limit: int = 5) -> list[dict]:
	"""Get recent interaction log entries."""
	return list(_interaction_log)[-limit:]


def get_relationship_summary() -> str:
	"""Build a human-readable summary of the relationship with the user."""
	with _lock:
		metrics = dict(_relationship_depth)
	emotions = get_emotional_states()
	prefs = get_user_preferences()
	topics = get_preferred_topics()
	parts = []
	depth = metrics.get("depth_score", 0)
	if depth >= 50:
		parts.append("Well-established relationship (strong rapport)")
	elif depth >= 25:
		parts.append("Growing relationship (getting to know each other)")
	elif depth >= 10:
		parts.append("New relationship (still building rapport)")
	else:
		parts.append("Initial interaction")
	if topics:
		parts.append(f"Interests: {', '.join(topics[:5])}")
	if emotions:
		recent_emotions = [e["emotion"] for e in emotions[-5:]]
		parts.append(f"Recent emotions: {', '.join(recent_emotions)}")
	if prefs:
		parts.append(f"Known preferences: {len(prefs)}")
	return ". ".join(parts) + "."


# --- Watcher functions (unchanged from original) ---


def run_checks(publish) -> list[str]:
	"""Run all awareness checks. Returns list of alert messages."""
	alerts = []

	# Battery
	try:
		battery_pct = int(_ps("(Get-WmiObject Win32_Battery).EstimatedChargeRemaining") or 100)
		if battery_pct <= 20:
			key = "battery_low"
			if should_notify(key):
				msg = f"Battery at {battery_pct}% - plug in soon."
				alerts.append(msg)
	except (ValueError, Exception):
		pass

	# Disk space
	try:
		free_gb = float(_ps("(Get-PSDrive C).Free / 1GB"))
		if free_gb < 10:
			key = "disk_low"
			if should_notify(key):
				alerts.append(f"Only {free_gb:.0f} GB free on C: - consider cleaning up.")
	except (ValueError, Exception):
		pass

	# Page changes
	watched_pages = db.get_setting("watched_pages", "")
	if watched_pages:
		for url in watched_pages.split(","):
			url = url.strip()
			if not url:
				continue
			try:
				from .tools.web_tools import fetch_page_text

				text = fetch_page_text(url, max_chars=2000)
				h = hashlib.sha256(text.encode()).hexdigest()[:12]
				prev = db.get_setting(f"pagehash_{hashlib.sha256(url.encode()).hexdigest()[:8]}", "")
				if prev != h:
					key = f"pagechange_{url[:50]}"
					if should_notify(key, 600):
						alerts.append(f"Page changed: {url[:60]}")
				db.set_setting(f"pagehash_{hashlib.sha256(url.encode()).hexdigest()[:8]}", h)
			except Exception:
				pass

	return alerts


def compose_briefing() -> str:
	"""Compose daily briefing from all available data."""
	now = datetime.now()
	parts = [f"Good {'morning' if now.hour < 12 else 'afternoon'}. It is {now.strftime('%H:%M')} on {now.strftime('%A, %d %B')}."]

	pending = db.reminders_pending()
	today_reminders = [
		r for r in pending
		if datetime.fromtimestamp(r["due_at"]).date() == now.date()
	]
	if today_reminders:
		parts.append(f"You have {len(today_reminders)} reminder(s): " +
					 "; ".join(r["text"][:40] for r in today_reminders[:3]) + ".")

	city = db.get_setting("city", "")

	from .vault import list_notes
	notes = list_notes()[:3]
	if notes:
		recent_topics = ", ".join(n["topic"] for n in notes)
		parts.append(f"Recent vault topics: {recent_topics}.")

	rel_summary = get_relationship_summary()
	if rel_summary:
		parts.append(f"Relationship context: {rel_summary}")

	parts.append("All systems nominal.")
	return " ".join(parts)


class AwarenessEngine:
	"""Runs checks periodically and publishes alerts."""

	def __init__(self) -> None:
		self._stop = __import__("threading").Event()
		self._thread: threading.Thread | None = None

	def start(self) -> None:
		self._thread = threading.Thread(target=self._loop, daemon=True, name="mk2-awareness")
		self._thread.start()

	def stop(self) -> None:
		self._stop.set()

	def _loop(self) -> None:
		log.info("awareness engine started")
		while not self._stop.is_set():
			try:
				from .fastlane import fast_command # ensure tools loaded

				alerts = run_checks(self._publish_alert)
				for alert in alerts:
					bus.publish("notify.out", {
						"kind": "watcher",
						"text": alert,
					})
			except Exception as exc:
				log.warning("awareness check failed: %s", exc)
			self._stop.wait(120) # every 2 minutes

	def _publish_alert(self, topic: str, payload: dict) -> None:
		bus.publish(topic, payload)

	def stop_and_wait(self) -> None:
		self._stop.set()


import queue as _q_mod

_queue_instance = _q_mod.Queue()


def _publish_alert_wrapper(topic: str, payload: dict) -> None:
	"""Thread-safe publish to the event bus."""
	from .bus import bus

	bus.publish(topic, payload)


_original_run_checks = run_checks


def run_checks_safe():
	return _original_run_checks(_publish_alert_wrapper)
