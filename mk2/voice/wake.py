"""Wake phrase matching."""
import re


def normalize(text: str) -> str:
	return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


_env_phrases = [p.strip().lower() for p in __import__("os").environ.get(
	"EVO_WAKE_PHRASES", "").split(",") if p.strip()]

DEFAULT_WAKE_PHRASES = [
	"evo", "hey evo", "ok evo", "wake up evo", "woke up evo", "wake up ever",
	"jarvis", "hey jarvis", "ok jarvis",
]

WAKE_PHRASES = sorted(set(DEFAULT_WAKE_PHRASES + _env_phrases), key=len, reverse=True)


def match_wake(text: str) -> str | None:
	"""Return remainder after a wake phrase, or None (min 3 chars after wake)."""
	t = normalize(text)
	if not t:
		return None

	for phrase in WAKE_PHRASES:
		if t == phrase:
			return ""
		if t.startswith(phrase + " "):
			rest = t[len(phrase) + 1:].strip()
			if len(rest) >= 3:
				return rest

	return None


def is_exit(text: str) -> bool:
	t = normalize(text)
	exits = ("stop listening", "go to sleep", "goodbye", "end session",
			 "that will be all")
	return any(e in t for e in exits)


_spotter = None


def get_spotter():
	global _spotter
	if _spotter is None:
		from .wake_spotter import WakeSpotter
		_spotter = WakeSpotter(WAKE_PHRASES)
	return _spotter
