"""Expanded fastlane: instant commands that never touch the LLM."""
import re
import webbrowser

from . import tools


def _normalize(text):
	t = (text or "").lower().strip(" .!?")
	t = re.sub(r"^(?:please|can you|could you|hey|evo|jarvis)\s+", "", t)
	t = re.sub(r"\s+(?:for me|please|now)$", "", t)
	return re.sub(r"\s+", " ", t).strip()


_ACTION_RE = re.compile(
	r"^(?:open|close|launch|search|google|look up|research|play|volume|mute|unmute|"
	r"screenshot|remind|reminder|set|take|capture|what|who|when|where|lock|sleep|"
	r"wifi|bluetooth|brightness|timer|translate|flip|roll)\b")


def _split_compound(t):
	parts = re.split(r"\s+(?:and|an|then)\s+|\s*;\s*", t)
	if len(parts) < 2:
		return [t]
	actionable = [p for p in parts if _ACTION_RE.search(p)]
	if len(actionable) == len(parts):
		return [p.strip() for p in parts]
	return [t]


def _youtube_search_url(query):
	from urllib.parse import quote_plus
	return f"https://www.youtube.com/results?search_query={quote_plus(query)}"


def fast_command(text, surface="console"):
	t = _normalize(text)
	if not t:
		return None
	parts = _split_compound(t)
	if len(parts) > 1:
		replies = []
		for part in parts:
			r = fast_command(part, surface=surface)
			if r:
				replies.append(r)
		if replies:
			return "; ".join(replies)
		return None
	return _single(t, surface=surface)


def _single(t, surface="console"):
	# time / date
	if re.search(r"\bwhat time\b|\bcurrent time\b|^time$", t):
		from datetime import datetime
		return datetime.now().strftime("%H:%M.")
	if re.search(r"\bwhat day\b|\bwhat.s the date\b|todays date|^date$", t):
		from datetime import datetime
		return datetime.now().strftime("%A, %d %B.")

	# screen read
	if re.search(r"(what.s? on my screen|whats on my screen|on my screen|read my screen|see my screen)", t):
		r = tools.call("screen_read")
		return r["speech"] if r.get("ok") else None

	# volume
	if re.fullmatch(r"(?:volume|sound) (?:up|louder)", t):
		return tools.call("volume", {"action": "up"}).get("speech")
	if re.fullmatch(r"(?:volume|sound) (?:down|quieter)", t):
		return tools.call("volume", {"action": "down"}).get("speech")
	if re.fullmatch(r"(?:mute|unmute)(?: the(?: sound| volume))?", t):
		return tools.call("volume", {"action": "mute"}).get("speech")

	# lock
	if re.fullmatch(r"lock (?:the )?(?:pc|computer|laptop|screen|workstation)", t):
		return tools.call("lock_pc").get("speech")

	# sleep
	if re.fullmatch(r"(?:go to )?sleep(?: the pc| the computer)?(?: now)?", t):
		return tools.call("sleep_pc", {"confirm": True}).get("speech")

	# WiFi
	if re.fullmatch(r"wifi (?:on|enable|connect)", t):
		return tools.call("wifi_toggle", {"state": "on"}).get("speech")
	if re.fullmatch(r"wifi (?:off|disable|disconnect)", t):
		return tools.call("wifi_toggle", {"state": "off"}).get("speech")
	if re.fullmatch(r"wifi status", t):
		return tools.call("wifi_status").get("speech")

	# Bluetooth
	if re.fullmatch(r"bluetooth (?:on|enable)", t):
		return tools.call("bluetooth_toggle", {"state": "on"}).get("speech")
	if re.fullmatch(r"bluetooth (?:off|disable)", t):
		return tools.call("bluetooth_toggle", {"state": "off"}).get("speech")
	if re.fullmatch(r"bluetooth status", t):
		return tools.call("bluetooth_status").get("speech")

	# brightness
	m = re.fullmatch(r"(?:set )?brightness (?:to )?(\d+)", t)
	if m:
		return tools.call("brightness_set", {"level": int(m.group(1))}).get("speech")
	if re.fullmatch(r"brightness(?: level)?", t):
		return tools.call("brightness_get").get("speech")

	# Direct website / app fast-path
	if t in ("youtube", "yt", "open youtube", "open yt", "launch youtube", "go to youtube"):
		webbrowser.open("https://www.youtube.com")
		return "Opening YouTube, sir."
	if t in ("github", "open github", "open gh"):
		webbrowser.open("https://github.com")
		return "Opening GitHub, sir."
	if t in ("gmail", "open gmail", "mail", "open mail"):
		webbrowser.open("https://mail.google.com")
		return "Opening Gmail, sir."
	if t in ("google", "open google"):
		webbrowser.open("https://www.google.com")
		return "Opening Google, sir."

	# play X on youtube
	m = re.fullmatch(r"(?:play|search|find|show) (.+?)(?: on youtube)?", t)
	if m and ("youtube" in t or t.startswith("play ")):
		q = m.group(1).strip()
		q = re.sub(r"^the best .{0,24}?(?:video|videos|clip)s? (?:of|about|on|for) ", "", q)
		q = re.sub(r"\s+(?:that |which )?you can find\b", " ", q)
		q = re.sub(r"\s+", " ", q).strip(" ,.")
		if q.lower() in ("the first video", "first video", "a video", "something", "anything", "videos", "youtube", "yt"):
			webbrowser.open("https://www.youtube.com")
			return "Opening YouTube, sir."
		url = _youtube_search_url(q)
		webbrowser.open(url)
		return f"Showing YouTube results for '{q}'."

	# open X
	m = re.fullmatch(r"open(?: up)? (?:the |my |an? )?(.+?)(?: app| website| site)?", t)
	if m:
		target = m.group(1).strip()
		if target in ("youtube", "yt"):
			webbrowser.open("https://www.youtube.com")
			return "Opening YouTube, sir."
		words = target.split()
		verb_hits = sum(1 for w in words if re.fullmatch(r"(?:play|search|find|show|look|give|tell|open|start|put|get)\b.*", w))
		if len(words) > 4 or (len(words) > 2 and verb_hits >= 1):
			return None
		r = tools.call("open_app", {"target": target})
		return r.get("speech")

	# close X
	m = re.fullmatch(r"close (?:the |my |all )?(.+)", t)
	if m:
		r = tools.call("close_app", {"target": m.group(1).strip()})
		return r.get("speech")

	# reminders
	reminder_text = None
	m = re.search(r"(remind me to .+|set a reminder.+)", t)
	if m:
		reminder_text = m.group(1).strip()
	elif t.startswith(("remind me", "reminder", "set a reminder")):
		reminder_text = t
	if reminder_text:
		from .timeparse import parse_when, strip_when
		from .. import db
		due = parse_when(reminder_text)
		body = strip_when(reminder_text)
		if due:
			db.reminder_add(body[:300], due.timestamp())
			return f"Reminder set for {due.strftime('%H:%M')} ({body[:60]})."
		return None

	# timers
	m = re.search(r"(?:set|start) (?:a )?timer (?:for )?(.+)", t)
	if m:
		dur = m.group(1).strip()
		label = m.group(1).strip() if "called" in t else "Timer"
		r = tools.call("timer_set", {"duration": dur, "label": label})
		return r.get("speech")

	# screenshot
	if re.fullmatch(r"(?:take a |capture a )?screenshot", t):
		r = tools.call("screenshot")
		return r.get("speech") if r.get("ok") else "Screenshot failed."

	# weather quick
	if re.fullmatch(r"(?:what.s|how is|current )?weather(?: like)?(?: outside| today)?(?: in (.+))?", t):
		m = re.search(r"in ([a-zA-Z\s]+)$", t)
		city = m.group(1).strip() if m else ""
		r = tools.call("weather_now", {"city": city})
		return r.get("speech")

	# coin flip
	if re.fullmatch(r"flip a coin", t):
		r = tools.call("coin_flip")
		return r.get("speech")

	# dice roll
	m = re.fullmatch(r"roll (\d+)?d(\d+)", t)
	if m:
		count = m.group(1) or "1"
		r = tools.call("dice_roll", {"dice": f"{count}d{m.group(2)}"})
		return r.get("speech")

	# who are you
	if re.search(r"\bwho are you\b|\bwhat are you\b|\byour name\b|\byou called\b", t):
		return ("I'm EVO, your personal assistant running locally on your PC. "
				"I can help with system control, web search, notes, reminders, "
				"translation, media, smart home devices, and more.")

	# what can you do / help
	if re.search(r"^(?:what can you do|what can you do for me|capabilities|"
				 r"help(?: me)?$|help$)", t):
		return ("I can control this PC (apps, WiFi, Bluetooth, brightness, lock, sleep), "
				"search the web, manage notes and reminders, set timers, translate text, "
				"control media and smart lights, summarize YouTube videos, read your screen, "
				"take screenshots, roll dice, flip coins, manage your todo list, "
				"check the weather, and more. Just ask.")

	# system status
	if re.search(r"^(?:system )?status(?: report)?$|\bhow.s everything\b"
				 r"|\bhow is the system\b|\bsystem health\b", t):
		try:
			r = tools.call("system_info")
			if r.get("ok"):
				return r.get("speech", "All systems nominal.")
			return "System status check failed."
		except Exception:
			return "I couldn't reach the system status right now."

	# todo add
	m = re.search(r"add (.+?) to (?:my )?(?:todo|task|to-do) list", t)
	if m:
		r = tools.call("todo_add", {"task": m.group(1).strip()})
		return r.get("speech")

	# gratitude / pleasantries
	if re.search(r"^(?:thank you(?: very much| so much)?|thanks(?: a lot| evo| jarvis)?|appreciate it|much appreciated|good job|great job|well done)$", t):
		import random
		return random.choice([
			"You're very welcome, sir. Always at your service.",
			"Happy to help, sir. Let me know if you need anything else.",
			"Anytime, sir. Standing by.",
			"My pleasure, sir. Let me know what you'd like to do next.",
		])

	return None
