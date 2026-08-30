"""Smart home tools: Home Assistant integration, thermostats, lights, scenes."""
import json
import logging
import os
import urllib.request
import urllib.error

from . import tool
from .. import db

log = logging.getLogger("mk2.smart_home")

HA_BASE = os.environ.get("EVO_HA_URL", "").rstrip("/")
HA_TOKEN = os.environ.get("EVO_HA_TOKEN", "")


def _ha_headers():
	h = {"Content-Type": "application/json"}
	if HA_TOKEN:
		h["Authorization"] = f"Bearer {HA_TOKEN}"
	return h


def _ha_get(path):
	if not HA_BASE:
		return None
	try:
		req = urllib.request.Request(f"{HA_BASE}{path}", headers=_ha_headers())
		with urllib.request.urlopen(req, timeout=5) as resp:
			return json.loads(resp.read().decode())
	except Exception:
		return None


def _ha_post(path, payload):
	if not HA_BASE:
		return None
	try:
		data = json.dumps(payload).encode()
		req = urllib.request.Request(f"{HA_BASE}{path}", data=data, headers=_ha_headers(), method="POST")
		with urllib.request.urlopen(req, timeout=5) as resp:
			return json.loads(resp.read().decode())
	except Exception as exc:
		log.warning("HA POST %s failed: %s", path, exc)
		return None


@tool("smart_light", "Control a smart light: on/off/toggle, optional brightness.",
	{"entity_id": {"type": "string"}, "action": {"type": "string", "enum": ["on", "off", "toggle"]},
	 "brightness": {"type": "integer", "default": 0}}, permission="execute")
def smart_light(entity_id="", action="toggle", brightness=0):
	if not HA_BASE:
		return {"ok": False, "speech": "Home Assistant not configured. Set EVO_HA_URL and EVO_HA_TOKEN.", "data": {}}
	if not entity_id:
		states = _ha_get("/api/states")
		if states:
			lights = [s["entity_id"] for s in states if s["entity_id"].startswith("light.")]
			if lights:
				return {"ok": True, "speech": f"Available lights: {', '.join(lights[:10])}", "data": {"lights": lights}}
		return {"ok": False, "speech": "No entity_id given and no lights found.", "data": {}}
	try:
		svc = "turn_on" if action == "on" else ("turn_off" if action == "off" else "toggle")
		payload = {"entity_id": entity_id}
		if action == "on" and brightness:
			payload["brightness_pct"] = min(100, max(0, brightness))
		result = _ha_post(f"/api/services/light/{svc}", payload)
		if result is not None:
			bright_str = f" at {brightness}%" if brightness else ""
			return {"ok": True, "speech": f"Light {entity_id}: {action}{bright_str}.", "data": {"entity_id": entity_id}}
		return {"ok": False, "speech": f"Home Assistant rejected the command.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Smart light failed: {exc}", "data": {}}


@tool("smart_lights_list", "List all smart lights and their current state.", {}, permission="read")
def smart_lights_list():
	if not HA_BASE:
		return {"ok": False, "speech": "Home Assistant not configured.", "data": {}}
	try:
		states = _ha_get("/api/states")
		if not states:
			return {"ok": False, "speech": "Could not reach Home Assistant.", "data": {}}
		lights = [s for s in states if s["entity_id"].startswith("light.")]
		if not lights:
			return {"ok": True, "speech": "No lights found in Home Assistant.", "data": {"lights": []}}
		lines = []
		for l in lights:
			state = l.get("state", "unknown")
			attrs = l.get("attributes", {})
			name = attrs.get("friendly_name", l["entity_id"])
			bright = attrs.get("brightness", "")
			if bright:
				bright_pct = round(bright / 255 * 100)
				lines.append(f"{name}: {state}, {bright_pct}%")
			else:
				lines.append(f"{name}: {state}")
		return {"ok": True, "speech": f"{len(lights)} light(s): " + "; ".join(lines[:15]),
				"data": {"lights": [{"entity_id": l["entity_id"], "state": l.get("state"),
					"name": l.get("attributes", {}).get("friendly_name", l["entity_id"])} for l in lights]}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not list lights: {exc}", "data": {}}


@tool("smart_scene", "Activate a Home Assistant scene by name.", {"scene": {"type": "string"}}, permission="execute")
def smart_scene(scene=""):
	if not HA_BASE:
		return {"ok": False, "speech": "Home Assistant not configured.", "data": {}}
	try:
		result = _ha_post("/api/services/scene/turn_on", {"entity_id": scene})
		if result is not None:
			return {"ok": True, "speech": f"Scene activated: {scene}", "data": {"scene": scene}}
		return {"ok": False, "speech": f"Could not activate scene: {scene}", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Scene failed: {exc}", "data": {}}


@tool("smart_status", "Get status of all smart home devices.", {}, permission="read")
def smart_status():
	if not HA_BASE:
		return {"ok": False, "speech": "Home Assistant not configured. Set EVO_HA_URL.", "data": {}}
	try:
		states = _ha_get("/api/states")
		if not states:
			return {"ok": False, "speech": "Could not reach Home Assistant.", "data": {}}
		lights = [s for s in states if s["entity_id"].startswith("light.")]
		sensors = [s for s in states if s["entity_id"].startswith("sensor.")]
		climate = [s for s in states if s["entity_id"].startswith("climate.")]
		summary = f"{len(lights)} lights, {len(sensors)} sensors, {len(climate)} climate devices."
		return {"ok": True, "speech": f"Smart home: {summary}",
				"data": {"lights": len(lights), "sensors": len(sensors), "climate": len(climate)}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get status: {exc}", "data": {}}


@tool("smart_temperature", "Get or set the thermostat temperature.",
	{"entity_id": {"type": "string", "default": ""},
	 "temperature": {"type": "number", "default": 0}}, permission="execute")
def smart_temperature(entity_id="", temperature=0):
	if not HA_BASE:
		return {"ok": False, "speech": "Home Assistant not configured.", "data": {}}
	try:
		if not entity_id:
			states = _ha_get("/api/states")
			if states:
				climate = [s["entity_id"] for s in states if s["entity_id"].startswith("climate.")]
				if climate:
					return {"ok": True, "speech": f"Climate devices: {', '.join(climate[:10])}", "data": {"devices": climate}}
			return {"ok": False, "speech": "No climate devices found.", "data": {}}
		if temperature:
			result = _ha_post("/api/services/climate/set_temperature",
							 {"entity_id": entity_id, "temperature": float(temperature)})
			if result is not None:
				return {"ok": True, "speech": f"Thermostat set to {temperature}C.", "data": {"entity_id": entity_id}}
		state = _ha_get(f"/api/states/{entity_id}")
		if state:
			attrs = state.get("attributes", {})
			cur = attrs.get("current_temperature", "?")
			tgt = attrs.get("temperature", "?")
			return {"ok": True, "speech": f"Thermostat: current {cur}C, target {tgt}C.",
					"data": {"current": cur, "target": tgt}}
		return {"ok": False, "speech": f"Could not get thermostat for {entity_id}.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Thermostat failed: {exc}", "data": {}}


@tool("ha_light_on", "Turn on a Home Assistant light.", {"entity": {"type": "string"}}, permission="execute")
def ha_light_on(entity: str) -> dict:
	return smart_light(entity_id=entity, action="on")


@tool("ha_light_off", "Turn off a Home Assistant light.", {"entity": {"type": "string"}}, permission="execute")
def ha_light_off(entity: str) -> dict:
	return smart_light(entity_id=entity, action="off")


@tool("ha_scene", "Activate a Home Assistant scene.", {"scene": {"type": "string"}}, permission="execute")
def ha_scene(scene: str) -> dict:
	return smart_scene(scene=scene)


@tool("ha_status", "Get status of all HA entities.", {}, permission="read")
def ha_status() -> dict:
	return smart_status()
