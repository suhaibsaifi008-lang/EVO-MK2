"""Phase 7: Initiative engine - EVO speaks first, within strict limits."""
import json
import logging
import os
import threading
import time
from datetime import datetime

from . import db

log = logging.getLogger("mk2.initiative")

_lock = threading.Lock()
_state = {"day": "", "count": 0, "last_ts": 0.0}

MIN_GAP_S = 5 * 60
RECENT_CONVERSATION_S = 5 * 60


def _limits():
	quiet_start = int(os.environ.get("EVO_QUIET_START", "23"))
	quiet_end = int(os.environ.get("EVO_QUIET_END", "8"))
	max_day = max(0, int(os.environ.get("EVO_INITIATIVE_MAX", "15")))
	return quiet_start, quiet_end, max_day


def _quiet_hours(now: datetime) -> bool:
	qs, qe, _ = _limits()
	return now.hour >= qs or now.hour < qe


def _user_recently_active() -> bool:
	rows = db.recent_messages(1)
	if not rows:
		return False
	import time as _t
	return (_t.time() - float(rows[0].get("ts") or 0)) < RECENT_CONVERSATION_S


def gather_candidates() -> list:
	"""Concrete, non-generic things worth mentioning."""
	out = []
	now = datetime.now()

	# 1. Upcoming calendar events in next 2 hours
	try:
		from .calendar_tools import calendar_today
		cal = calendar_today()
		if cal.get("ok") and cal.get("data", {}).get("events"):
			events = cal["data"]["events"]
			soon = []
			for e in events:
				e_start = e.get("start")
				if isinstance(e_start, str):
					try:
						e_start = datetime.fromisoformat(e_start)
					except Exception:
						continue
				elif not isinstance(e_start, datetime):
					continue
				delta = (e_start - now).total_seconds()
				if 0 <= delta <= 7200:
					soon.append((delta, e.get("summary", "Untitled event"), e_start))
			soon.sort(key=lambda x: x[0])
			if soon:
				delta_sec, summary, e_start = soon[0]
				mins = int(delta_sec // 60)
				if mins < 5:
					out.append(f"Heads up - '{summary}' starts in {mins} minutes!")
				elif mins < 60:
					out.append(f"You have '{summary}' in {mins} minutes. Want me to prepare anything?")
				else:
					out.append(f"Coming up at {e_start.strftime('%H:%M')}: '{summary}'.")
			elif events:
				today_events = events[:5]
				out.append(f"You have {len(today_events)} calendar event(s) today: " +
						 "; ".join(e.get("summary", "?") for e in today_events))
	except Exception:
		pass

	# 2. Unread emails (if mail is configured)
	try:
		from .tools.life_tools import mail_check
		mail = mail_check()
		if mail.get("ok"):
			unread = mail.get("unread", 0)
			if unread > 0:
				out.append(f"You have {unread} unread email(s). "
						 f"Latest: {mail.get('subject', 'unknown subject')}.")
	except Exception:
		pass

	# 3. Long-running tasks that completed (check module-level event buffer)
	try:
		_skip_kinds = {"initiative", "voice", "telegram", "watcher", "autonomy", "job", "task", "reminder", "timer", "dev"}
		completed = []
		for ts, ev in _initiative_event_buffer:
			if ts >= _initiative_last_check_ts:
				kind = ev.get("kind", "")
				if kind not in _skip_kinds:
					text = ev.get("text", kind)[:80]
					completed.append(f"{kind}: {text}")
		if completed:
			_initiative_update_check()
			out.append(f"Completed: {'; '.join(completed[:2])}.")
	except Exception:
		pass

	# 4. Proactive Morning & Evening Briefings
	try:
		from . import proactive_briefing, perception, user_profile

		# Ambient perception triggers
		ambient = perception.get_ambient_context()
		if ambient.get("activity") == "coding" and now.hour >= 22:
			out.append("It's getting late, sir. Shall I record your active project state and prepare tomorrow's morning briefing?")

		if ambient.get("battery_pct") is not None and ambient.get("battery_pct") < 20:
			out.append(f"Battery is low at {ambient['battery_pct']}%. Should I throttle non-essential background tasks?")

		# Morning briefing (pre-composed)
		if now.hour in (7, 8) and now.minute < 30:
			briefing = proactive_briefing.compose_morning_briefing()
			if briefing:
				out.insert(0, briefing)
		elif now.hour == 21 and now.minute < 30:
			evening = proactive_briefing.compose_evening_briefing()
			if evening:
				out.append(evening)

		# Active Goal Continuation
		prof = user_profile.get_user_profile()
		for g in prof.get("goals", []):
			if g.get("name") and g.get("progress", 0) < 100 and now.hour in (9, 10):
				out.append(f"Good morning. Active goal '{g['name']}' is at {g.get('progress', 0)}% progress. Shall we work on it?")
				break
	except Exception:
		pass

	# Reminders (existing logic)
	try:
		pending = db.reminders_pending()
		if len(pending) >= 3:
			first = pending[0]["text"][:60]
			out.append(f"You have {len(pending)} reminders pending - next up: '{first}'.")
		elif len(pending) == 1:
			out.append(f"Reminder coming up: {pending[0]['text'][:60]}.")
	except Exception:
		pass

	# Pending proposals (existing logic)
	try:
		proposals = db.proposals(status="pending")
		if proposals:
			out.append(f"There {'is' if len(proposals) == 1 else 'are'} "
					 f"{len(proposals)} automation suggestion(s) waiting for your yes.")
	except Exception:
		pass

	# Predictive Anticipation: recurring learned patterns with >70% confidence
	try:
		from . import patterns
		upcoming = patterns.predict_upcoming_patterns(lookahead_minutes=25)
		for pat in upcoming[:2]:
			mins_left = pat.get("minutes_until", 0)
			action_hint = pat.get("action_hint", pat.get("type", ""))
			if mins_left <= 5:
				out.append(f"Heads up, sir: usually around this time you {action_hint}. Shall I set that up?")
			else:
				out.append(f"In about {mins_left} minutes: {action_hint}. Would you like me to prepare anything?")
			patterns.mark_pattern_triggered(pat["id"])
	except Exception:
		pass

	return out


# Track last initiative check time for bus event scanning
_initiative_last_check_ts = 0.0
_initiative_event_buffer: list = []

def _initiative_last_check():
	global _initiative_last_check_ts
	return _initiative_last_check_ts

def _initiative_update_check():
	global _initiative_last_check_ts
	_initiative_last_check_ts = time.time()

def _initiative_record_event(ev):
	"""Called by bus subscribers to record events for initiative scanning."""
	try:
		if hasattr(ev, "payload"):
			payload = ev.payload
			topic = ev.topic
		else:
			payload = ev
			topic = ""
		rec = {
			"kind": payload.get("kind", topic) if isinstance(payload, dict) else str(ev),
			"text": (payload.get("text", "")[:80] if isinstance(payload, dict) else ""),
		}
		_initiative_event_buffer.append((time.time(), rec))
	except Exception:
		pass
	# Keep buffer manageable
	if len(_initiative_event_buffer) > 200:
		_initiative_event_buffer[:] = _initiative_event_buffer[-100:]


_bus_subscribed = False


def _initiative_subscribe_bus():
	"""Subscribe to notify.out events so we can track completed tasks."""
	global _bus_subscribed
	if _bus_subscribed:
		return
	try:
		from .bus import bus
		def _on_notify(ev):
			try:
				_initiative_record_event(ev)
			except Exception:
				pass
		bus.subscribe("notify.out", callback=_on_notify)
		_bus_subscribed = True
	except Exception:
		pass


_initiative_subscribe_bus()


def compose(candidate: str) -> str:
	"""Turn a raw candidate into one natural, human sentence."""
	try:
		from . import llm
		return llm.chat(
			[{"role": "system",
			 "content": ("Turn this into ONE short, natural message from EVO to its user. "
						 "Casual, warm, no emoji spam, no 'sir', max 2 sentences. "
						 "Be specific, not generic.")},
			 {"role": "user", "content": candidate[:400]}],
			role="fast", temperature=0.6, timeout=12)
	except Exception:
		return candidate


def maybe_initiate(publish) -> bool:
	"""Called by the kernel tick. Returns True when it spoke."""
	_initiative_subscribe_bus()
	now = datetime.now()
	qs, qe, max_day = _limits()
	with _lock:
		today = now.strftime("%Y%m%d")
		if _state["day"] != today:
			_state["day"], _state["count"] = today, 0
		if _state["count"] >= max_day:
			return False
		import time as _t
		if _t.time() - _state["last_ts"] < MIN_GAP_S:
			return False
	if _quiet_hours(now) or _user_recently_active():
		return False
	candidates = gather_candidates()
	if not candidates:
		return False
	message = compose(candidates[0])
	publish("notify.out", {"kind": "initiative", "text": message})
	with _lock:
		_state["count"] += 1
		_state["last_ts"] = __import__("time").time()
	try:
		db.audit("initiative", candidates[0][:100], True, message[:120])
	except Exception:
		pass
	return True


def status() -> dict:
	with _lock:
		return {"today_count": _state["count"],
				"max": _limits()[2],
				"quiet_hours": f"{_limits()[0]}:00-{_limits()[1]}:00"}


from .tools import tool


@tool("initiative_now", "Make EVO say something genuinely useful right now (one unprompted thought).",
	{}, permission="read")
def initiative_now() -> dict:
	candidates = gather_candidates()
	if not candidates:
		return {"ok": False,
				"speech": "Nothing interesting enough to bring up right now.",
				"data": {}}
	msg = compose(candidates[0])
	return {"ok": True, "speech": msg, "data": {"candidate": candidates[0]}}


@tool("morning_briefing", "Get a complete morning briefing: weather, calendar, reminders, news.",
	{"city": {"type": "string", "default": ""}}, permission="read")
def morning_briefing(city=""):
	try:
		parts = []
		now = datetime.now()
		greeting = "Good morning" if now.hour < 12 else ("Good afternoon" if now.hour < 17 else "Good evening")
		parts.append(f"{greeting}. It is {now.strftime('%H:%M')} on {now.strftime('%A, %d %B %Y')}.")

		# Calendar
		try:
			from ..calendar_tools import calendar_today
			cal = calendar_today()
			if cal.get("ok") and cal.get("data", {}).get("events"):
				events = cal["data"]["events"][:5]
				parts.append(f"You have {len(events)} event(s) today: " +
							 "; ".join(e.get("summary", "?") for e in events))
			elif cal.get("ok"):
				parts.append("No calendar events today.")
		except Exception:
			pass

		# Reminders
		try:
			pending = db.reminders_pending()
			today_pending = [r for r in pending
							if datetime.fromtimestamp(r["due_at"]).date() == now.date()]
			if today_pending:
				parts.append(f"{len(today_pending)} reminder(s) pending.")
		except Exception:
			pass

		# Weather
		try:
			from .tools.life_tools import weather_now as _wn
			wr = _wn(city)
			if wr.get("ok"):
				parts.append(wr["speech"])
		except Exception:
			pass

		# Vault notes
		try:
			from .vault import list_notes
			notes = list_notes()[:3]
			if notes:
				recent_topics = ", ".join(n["topic"] for n in notes)
				parts.append(f"Recent vault topics: {recent_topics}.")
		except Exception:
			pass

		parts.append("All systems nominal.")
		speech = " ".join(parts)
		return {"ok": True, "speech": speech, "data": {"briefing": speech}}
	except Exception as exc:
		return {"ok": False, "speech": f"Briefing failed: {exc}", "data": {}}


@tool("evening_summary", "Get an end-of-day summary: what happened, what's pending.",
	{}, permission="read")
def evening_summary():
	try:
		parts = []
		now = datetime.now()
		parts.append(f"Evening summary - {now.strftime('%A, %d %B %Y')}.")

		# Today's reminders fired
		try:
			today_start = now.replace(hour=0, minute=0, second=0).timestamp()
			pending = db.reminders_pending()
			fired = [r for r in pending if r.get("fired_at", 0) >= today_start]
			if fired:
				parts.append(f"{len(fired)} reminder(s) completed today.")
		except Exception:
			pass

		# Recent conversation count
		try:
			messages = db.recent_messages(20)
			user_msgs = [m for m in messages if m.get("role") == "user"]
			if user_msgs:
				parts.append(f"{len(user_msgs)} interactions today.")
		except Exception:
			pass

		# Vault activity
		try:
			from .vault import list_notes
			notes = list_notes()
			today_notes = [n for n in notes if n.get("created", "").startswith(now.strftime("%Y-%m-%d"))]
			if today_notes:
				parts.append(f"{len(today_notes)} note(s) created today.")
		except Exception:
			pass

		parts.append("Rest well. All systems nominal.")
		speech = " ".join(parts)
		return {"ok": True, "speech": speech, "data": {"summary": speech}}
	except Exception as exc:
		return {"ok": False, "speech": f"Summary failed: {exc}", "data": {}}
