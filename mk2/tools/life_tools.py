"""Lifestyle tools: weather, todos, notes, translation, timers, media control, utilities."""
import json
import logging
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from . import tool
from .. import db

log = logging.getLogger("mk2.life_tools")

_DATA = Path(__file__).resolve().parent.parent.parent / "data"
_TODOS_FILE = _DATA / "todos.json"
_NOTES_FILE = _DATA / "quick_notes.json"


def _load_json(path, default):
	if path.exists():
		try:
			return json.loads(path.read_text())
		except Exception:
			pass
	return default


def _save_json(path, data):
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(json.dumps(data, indent=2, default=str))


@tool("weather_now", "Get current weather for a city (free, no key needed).",
	{"city": {"type": "string", "default": ""}}, permission="read")
def weather_now(city=""):
	try:
		from urllib.parse import quote
		import urllib.request
		city_q = quote(city) if city else ""
		url = f"wttr.in/{city_q}?format=j1&lang=en" if city_q else "wttr.in/?format=j1&lang=en"
		req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
		with urllib.request.urlopen(req, timeout=10) as resp:
			data = json.loads(resp.read().decode())
		current = data["current_condition"][0]
		area = data["nearest_area"][0]
		city_name = area.get("areaName", [{}])[0].get("value", city or "here")
		temp = current.get("temp_C", "?")
		feels = current.get("FeelsLikeC", "?")
		humidity = current.get("humidity", "?")
		desc = current.get("weatherDesc", [{}])[0].get("value", "unknown")
		wind = current.get("windspeedKmph", "?")
		speech = (f"Weather in {city_name}: {desc}, {temp}C (feels like {feels}C), "
				 f"humidity {humidity}%, wind {wind} km/h.")
		return {"ok": True, "speech": speech, "data": {"city": city_name, "temp": temp,
				"desc": desc, "humidity": humidity, "wind": wind}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get weather: {exc}", "data": {}}


@tool("weather_forecast", "Get 3-day weather forecast for a city.",
	{"city": {"type": "string", "default": ""},
	 "days": {"type": "integer", "default": 3}}, permission="read")
def weather_forecast(city="", days=3):
	try:
		from urllib.parse import quote
		import urllib.request
		city_q = quote(city) if city else ""
		url = f"wttr.in/{city_q}?format=j1&lang=en" if city_q else "wttr.in/?format=j1&lang=en"
		req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
		with urllib.request.urlopen(req, timeout=10) as resp:
			data = json.loads(resp.read().decode())
		area = data["nearest_area"][0]
		city_name = area.get("areaName", [{}])[0].get("value", city or "here")
		days_data = data.get("weather", [])[:max(1, days)]
		forecast = []
		for d in days_data:
			date = d.get("date", "?")
			maxt = d.get("maxtempC", "?")
			mint = d.get("mintempC", "?")
			hourly = d.get("hourly", [])
			desc = hourly[4].get("weatherDesc", [{}])[0].get("value", "?") if len(hourly) > 4 else "?"
			forecast.append(f"{date}: {desc}, {mint}C to {maxt}C")
		speech = f"Forecast for {city_name}: " + " | ".join(forecast)
		return {"ok": True, "speech": speech, "data": {"city": city_name, "forecast": forecast}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get forecast: {exc}", "data": {}}


@tool("todo_add", "Add a task to your todo list.",
	{"task": {"type": "string"},
	 "priority": {"type": "string", "enum": ["low", "normal", "high"], "default": "normal"}},
	permission="read")
def todo_add(task="", priority="normal"):
	try:
		todos = _load_json(_TODOS_FILE, {"items": []})
		item = {
			"id": int(time.time() * 1000) % 10**9,
			"task": task, "priority": priority, "done": False,
			"created": datetime.now().isoformat(),
		}
		todos["items"].append(item)
		_save_json(_TODOS_FILE, todos)
		return {"ok": True, "speech": f"Added: {task}", "data": {"id": item["id"]}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not add todo: {exc}", "data": {}}


@tool("todo_list", "List your todo items.",
	{"show_done": {"type": "boolean", "default": False}}, permission="read")
def todo_list(show_done=False):
	try:
		todos = _load_json(_TODOS_FILE, {"items": []})
		items = todos.get("items", [])
		if not show_done:
			items = [i for i in items if not i.get("done")]
		if not items:
			return {"ok": True, "speech": "Todo list is empty.", "data": {"items": []}}
		items.sort(key=lambda x: (0 if x.get("priority") == "high" else 1, x.get("created", "")))
		lines = [f"{'[x]' if i.get('done') else '[ ]'} {i['task']}" for i in items[:20]]
		speech = f"{len(items)} item(s): " + "; ".join(lines)
		return {"ok": True, "speech": speech, "data": {"items": items[:20]}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not list todos: {exc}", "data": {}}


@tool("todo_complete", "Mark a todo as done by matching its text.",
	{"target": {"type": "string"}}, permission="read")
def todo_complete(target=""):
	try:
		todos = _load_json(_TODOS_FILE, {"items": []})
		target_lower = target.lower()
		for item in todos.get("items", []):
			if target_lower in item.get("task", "").lower():
				item["done"] = True
				_save_json(_TODOS_FILE, todos)
				return {"ok": True, "speech": f"Done: {item['task']}", "data": {"id": item["id"]}}
		return {"ok": False, "speech": f"No todo matching '{target}'.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not complete todo: {exc}", "data": {}}


@tool("todo_clear", "Clear all completed todos.", {}, permission="execute")
def todo_clear():
	try:
		todos = _load_json(_TODOS_FILE, {"items": []})
		n_before = len(todos["items"])
		todos["items"] = [i for i in todos.get("items", []) if not i.get("done")]
		n_after = len(todos["items"])
		_save_json(_TODOS_FILE, todos)
		return {"ok": True, "speech": f"Cleared {n_before - n_after} done items. {n_after} remaining.",
				"data": {"remaining": n_after}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not clear: {exc}", "data": {}}


@tool("note_quick", "Jot a quick note with optional tags.",
	{"content": {"type": "string"}, "tags": {"type": "string", "default": ""}},
	permission="read")
def note_quick(content="", tags=""):
	try:
		notes = _load_json(_NOTES_FILE, [])
		note = {
			"id": int(time.time() * 1000) % 10**9,
			"content": content,
			"tags": [t.strip() for t in tags.split(",") if t.strip()],
			"created": datetime.now().isoformat(),
		}
		notes.append(note)
		_save_json(_NOTES_FILE, notes)
		return {"ok": True, "speech": f"Note saved: {content[:60]}", "data": {"id": note["id"]}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not save note: {exc}", "data": {}}


@tool("note_list", "List recent quick notes.",
	{"limit": {"type": "integer", "default": 10}}, permission="read")
def note_list(limit=10):
	try:
		notes = _load_json(_NOTES_FILE, [])
		recent = sorted(notes, key=lambda x: x.get("created", ""), reverse=True)[:max(1, limit)]
		if not recent:
			return {"ok": True, "speech": "No notes yet.", "data": {"notes": []}}
		lines = [f"{n.get('created', '?')[:16]}: {n['content'][:80]}" for n in recent]
		speech = f"{len(recent)} note(s): " + "; ".join(lines)
		return {"ok": True, "speech": speech, "data": {"notes": recent}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not list notes: {exc}", "data": {}}


_timers = {}
_timer_lock = threading.Lock()
_next_tid = [1]


@tool("timer_set", "Set a timer. when: 'in 10 minutes', 'in 30 seconds', 'in 2 hours'.",
	{"duration": {"type": "string"}, "label": {"type": "string", "default": "Timer"}},
	permission="read")
def timer_set(duration="5 minutes", label="Timer"):
	try:
		from .work_tools import parse_when
		due = parse_when(f"{label} {duration}")
		if not due:
			return {"ok": False, "speech": f"Could not parse '{duration}'. Try 'in 10 minutes'.", "data": {}}
		tid = _next_tid[0]
		_next_tid[0] += 1
		with _timer_lock:
			_timers[tid] = {"label": label, "due": due.isoformat(), "fired": False}

		def _fire():
			wait = max(0, (due - datetime.now()).total_seconds())
			time.sleep(wait)
			with _timer_lock:
				t = _timers.get(tid)
				if t and not t["fired"]:
					t["fired"] = True
					try:
						from ..bus import bus
						bus.publish("notify.out", {"kind": "timer", "text": f"Timer: {label}"})
					except Exception:
						pass

		threading.Thread(target=_fire, daemon=True, name=f"timer-{tid}").start()
		mins = round((due - datetime.now()).total_seconds() / 60, 1)
		return {"ok": True, "speech": f"Timer set: {label} for {mins} minutes.",
				"data": {"id": tid, "due": due.isoformat()}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not set timer: {exc}", "data": {}}


@tool("timer_list", "List active timers.", {}, permission="read")
def timer_list():
	try:
		with _timer_lock:
			active = [{"id": k, "label": v["label"], "due": v["due"]}
					 for k, v in _timers.items() if not v["fired"]]
		if not active:
			return {"ok": True, "speech": "No active timers.", "data": {"timers": []}}
		return {"ok": True, "speech": f"{len(active)} active timer(s).", "data": {"timers": active}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not list timers: {exc}", "data": {}}


@tool("translate", "Translate text to another language.",
	{"text": {"type": "string"}, "to_lang": {"type": "string", "default": "hindi"}},
	permission="read")
def translate(text="", to_lang="hindi"):
	try:
		lang_map = {
			"hindi": "Hindi", "spanish": "Spanish", "french": "French", "german": "German",
			"japanese": "Japanese", "korean": "Korean", "chinese": "Chinese",
			"arabic": "Arabic", "portuguese": "Portuguese", "russian": "Russian",
			"bengali": "Bengali", "tamil": "Tamil", "telugu": "Telugu",
			"marathi": "Marathi", "gujarati": "Gujarati", "punjabi": "Punjabi",
			"urdu": "Urdu", "english": "English", "italian": "Italian",
		}
		target = lang_map.get(to_lang.lower(), to_lang)
		prompt = f"Translate to {target}. Return ONLY the translation:\n\n{text[:500]}"
		from ..llm import chat
		result = chat([{"role": "user", "content": prompt}], temperature=0.3, role="fast", timeout=15)
		return {"ok": True, "speech": f"Translation ({target}): {result}",
				"data": {"original": text[:200], "translated": result, "language": target}}
	except Exception as exc:
		return {"ok": False, "speech": f"Translation failed: {exc}", "data": {}}


@tool("media_control", "Control media: play, pause, next, previous, volume up/down, mute.",
	{"action": {"type": "string", "enum": ["play", "pause", "next", "previous",
			"volume_up", "volume_down", "mute"]}}, permission="execute")
def media_control(action="play"):
	try:
		import ctypes
		VK = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1,
			 "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}
		key = VK.get(action)
		if key is None:
			return {"ok": False, "speech": f"Unknown action: {action}", "data": {}}
		ctypes.windll.user32.keybd_event(key, 0, 0, 0)
		ctypes.windll.user32.keybd_event(key, 0, 2, 0)
		labels = {"play": "Playing", "pause": "Paused", "next": "Next track",
				 "previous": "Previous track", "volume_up": "Volume up",
				 "volume_down": "Volume down", "mute": "Muted"}
		return {"ok": True, "speech": labels.get(action, f"Did {action}"), "data": {"action": action}}
	except Exception as exc:
		return {"ok": False, "speech": f"Media control failed: {exc}", "data": {}}


@tool("unit_convert", "Convert between units: '100 km to miles', '5 kg to pounds', '32F to C'.",
	{"value": {"type": "number"}, "from_unit": {"type": "string"}, "to_unit": {"type": "string"}},
	permission="read")
def unit_convert(value=1, from_unit="", to_unit=""):
	try:
		conversions = {
			("km", "miles"): lambda v: v * 0.621371,
			("miles", "km"): lambda v: v * 1.60934,
			("kg", "pounds", "lbs"): lambda v: v * 2.20462,
			("pounds", "kg", "lbs"): lambda v: v * 0.453592,
			("celsius", "fahrenheit", "c", "f"): lambda v: v * 9/5 + 32,
			("fahrenheit", "celsius", "f", "c"): lambda v: (v - 32) * 5/9,
			("liters", "gallons"): lambda v: v * 0.264172,
			("gallons", "liters"): lambda v: v * 3.78541,
			("meters", "feet"): lambda v: v * 3.28084,
			("feet", "meters"): lambda v: v * 0.3048,
			("inches", "cm"): lambda v: v * 2.54,
			("cm", "inches"): lambda v: v * 0.393701,
		}
		fu = from_unit.lower().strip()
		tu = to_unit.lower().strip()
		for key_tuple, conv_fn in conversions.items():
			if fu in key_tuple and tu in key_tuple:
				result = conv_fn(float(value))
				return {"ok": True, "speech": f"{value} {from_unit} = {result:.2f} {to_unit}",
						"data": {"from_value": value, "from_unit": from_unit,
								 "to_value": round(result, 4), "to_unit": to_unit}}
		return {"ok": False, "speech": f"Don't know how to convert {from_unit} to {to_unit}.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Conversion failed: {exc}", "data": {}}


@tool("dice_roll", "Roll dice. format: NdN (e.g. 2d6, 1d20).",
	{"dice": {"type": "string", "default": "1d6"}}, permission="read")
def dice_roll(dice="1d6"):
	try:
		m = re.match(r"(\d+)[dD](\d+)", dice)
		if not m:
			return {"ok": False, "speech": "Use format like 2d6 or 1d20.", "data": {}}
		count, sides = int(m.group(1)), int(m.group(2))
		count = min(count, 100)
		rolls = [random.randint(1, sides) for _ in range(count)]
		total = sum(rolls)
		detail = ", ".join(str(r) for r in rolls) if count <= 10 else f"{count} dice"
		return {"ok": True, "speech": f"Rolled {dice}: {detail} = {total}",
				"data": {"rolls": rolls, "total": total}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not roll dice: {exc}", "data": {}}


@tool("coin_flip", "Flip a coin.", {}, permission="read")
def coin_flip():
	result = random.choice(["Heads", "Tails"])
	return {"ok": True, "speech": f"It's {result}!", "data": {"result": result}}


@tool("password_generate", "Generate a secure random password.",
	{"length": {"type": "integer", "default": 16}}, permission="read")
def password_generate(length=16):
	try:
		import string, secrets
		length = max(8, min(128, int(length)))
		chars = string.ascii_letters + string.digits + "!@#$%^&*"
		pwd = "".join(secrets.choice(chars) for _ in range(length))
		return {"ok": True, "speech": f"Generated {length}-character password.",
				"data": {"password": pwd}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not generate password: {exc}", "data": {}}


@tool("currency_convert", "Convert between currencies (approximate rates).",
	{"amount": {"type": "number"}, "from_currency": {"type": "string"}, "to_currency": {"type": "string"}},
	permission="read")
def currency_convert(amount=1, from_currency="USD", to_currency="INR"):
	try:
		rates = {
			"USD": 1, "EUR": 0.92, "GBP": 0.79, "INR": 83.12, "JPY": 149.50,
			"AUD": 1.52, "CAD": 1.36, "CNY": 7.24, "KRW": 1320, "BRL": 4.97,
		}
		fu = from_currency.upper().strip()
		tu = to_currency.upper().strip()
		if fu not in rates or tu not in rates:
			return {"ok": False, "speech": "Unsupported currency. Use 3-letter codes (USD, EUR, INR...).", "data": {}}
		usd_amount = float(amount) / rates[fu]
		result = usd_amount * rates[tu]
		return {"ok": True, "speech": f"{amount} {fu} = {result:.2f} {tu}",
				"data": {"from": amount, "from_currency": fu, "to": round(result, 2), "to_currency": tu}}
	except Exception as exc:
		return {"ok": False, "speech": f"Currency conversion failed: {exc}", "data": {}}


@tool("time_in", "Get current time in a timezone or city.",
	{"location": {"type": "string", "default": "local"}}, permission="read")
def time_in(location="local"):
	try:
		if location.lower() in ("local", "here", ""):
			now = datetime.now()
			speech = f"Local time is {now.strftime('%H:%M:%S')} on {now.strftime('%A, %d %B %Y')}."
			return {"ok": True, "speech": speech, "data": {"time": now.isoformat(), "location": "local"}}
		import urllib.request
		url = f"wttr.in/{location}?format=%l:+%c+%t+%h+%w+%p\n"
		req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
		with urllib.request.urlopen(req, timeout=5) as resp:
			data = resp.read().decode().strip()
		return {"ok": True, "speech": f"Time in {location}: {data}", "data": {"raw": data}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get time: {exc}", "data": {}}
