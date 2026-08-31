"""System management tools: processes, system info, power, network, display, alarms."""
import json
import logging
import platform
import psutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import tool
from .. import db

log = logging.getLogger("mk2.system_ext")


def _ps(script, timeout=15):
	r = subprocess.run(
		["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
		capture_output=True, text=True, timeout=timeout,
		creationflags=subprocess.CREATE_NO_WINDOW,
	)
	return (r.stdout or "").strip()


@tool("process_list", "List running processes sorted by memory usage.",
	{"limit": {"type": "integer", "default": 30}}, permission="read")
def process_list(limit=30):
	try:
		procs = []
		for p in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
			try:
				info = p.info
				mem_mb = (info["memory_info"].rss or 0) / 1024 / 1024
				procs.append({
					"pid": info["pid"],
					"name": info["name"] or "?",
					"mem_mb": round(mem_mb, 1),
					"cpu": round(info["cpu_percent"] or 0, 1),
				})
			except (psutil.NoSuchProcess, psutil.AccessDenied):
				continue
		procs.sort(key=lambda x: x["mem_mb"], reverse=True)
		top = procs[:max(1, limit)]
		top_name = top[0]["name"] if top else "?"
		return {"ok": True, "speech": f"{len(top)} processes. Top: {top_name} at {top[0]['mem_mb']} MB.",
				"data": {"processes": top}}
	except Exception as exc:
		return {"ok": False, "speech": f"Failed: {exc}", "data": {}}


_PROTECTED_PROCESSES = {
    "lsass", "csrss", "winlogon", "services", "wininit", "smss",
    "system", "registry", "dwm", "svchost", "fontdrvhost",
    "lsaiso", "ntoskrnl", "conhost",
}

@tool("process_kill", "Kill a process by name or PID.",
	{"target": {"type": "string"}}, permission="execute")
def process_kill(target=""):
	try:
		killed = []
		target_int = None
		try:
			target_int = int(target.strip())
		except ValueError:
			pass
		for p in psutil.process_iter(["pid", "name"]):
			try:
				name = (p.info["name"] or "").lower()
				pid = p.info["pid"]
				match = (target_int is not None and pid == target_int) or \
						 (target_int is None and target.lower() in name)
				if match:
					if name in _PROTECTED_PROCESSES or pid < 100:
						continue  # skip critical system processes
					p.terminate()
					killed.append(f"{name} (pid {pid})")
			except (psutil.NoSuchProcess, psutil.AccessDenied):
				continue
		if killed:
			return {"ok": True, "speech": f"Killed: {', '.join(killed)}.", "data": {"killed": killed}}
		return {"ok": False, "speech": f"No process matching '{target}' found.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Failed: {exc}", "data": {}}


@tool("system_info", "Get system info: CPU, RAM, disk, OS, uptime.", {}, permission="read")
def system_info():
	try:
		import socket
		boot = psutil.boot_time()
		uptime_s = time.time() - boot
		cpu_pct = psutil.cpu_percent(interval=0.5)
		mem = psutil.virtual_memory()
		disk = psutil.disk_usage("C:\\")
		info = {
			"hostname": socket.gethostname(),
			"os": f"Windows {platform.version()}",
			"cpu_percent": round(cpu_pct, 1),
			"cpu_cores": psutil.cpu_count(logical=False),
			"ram_total_gb": round(mem.total / 1024**3, 1),
			"ram_used_gb": round(mem.used / 1024**3, 1),
			"ram_percent": round(mem.percent, 1),
			"disk_free_gb": round(disk.free / 1024**3, 1),
			"disk_percent": round(disk.percent, 1),
			"uptime_hours": round(uptime_s / 3600, 1),
		}
		speech = (f"System: {info['hostname']}. CPU {info['cpu_percent']}%, "
				 f"RAM {info['ram_used_gb']}/{info['ram_total_gb']} GB ({info['ram_percent']}%), "
				 f"Disk C: {info['disk_free_gb']} GB free ({info['disk_percent']}% used). "
				 f"Uptime {info['uptime_hours']} hours.")
		return {"ok": True, "speech": speech, "data": info}
	except Exception as exc:
		return {"ok": False, "speech": f"Failed: {exc}", "data": {}}


@tool("lock_pc", "Lock the Windows workstation.", {}, permission="execute")
def lock_pc():
	try:
		import ctypes
		ctypes.windll.user32.LockWorkStation()
		return {"ok": True, "speech": "PC locked.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not lock: {exc}", "data": {}}


@tool("wifi_status", "Get WiFi status: connected SSID and signal.", {}, permission="read")
def wifi_status():
	try:
		out = _ps("netsh wlan show interfaces")
		ssid, signal = "", ""
		for line in out.splitlines():
			line = line.strip()
			if "SSID" in line and ":" in line and not line.startswith("SSID name"):
				parts = line.split(":", 1)
				if len(parts) == 2:
					ssid = parts[1].strip()
			if "Signal" in line and "%" in line:
				parts = line.split(":", 1)
				if len(parts) == 2:
					signal = parts[1].strip()
		if ssid:
			return {"ok": True, "speech": f"Connected to {ssid}, signal {signal}.", "data": {"ssid": ssid, "signal": signal}}
		return {"ok": True, "speech": "Not connected to WiFi.", "data": {"ssid": "", "signal": ""}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get WiFi: {exc}", "data": {}}


@tool("wifi_toggle", "Turn WiFi on or off.",
	{"state": {"type": "string", "enum": ["on", "off"]}}, permission="execute")
def wifi_toggle(state="on"):
	try:
		out = _ps("netsh wlan show interfaces | findstr 'Name'")
		iface = out.split(":")[-1].strip() if ":" in out else ""
		if not iface:
			return {"ok": False, "speech": "No WiFi interface found.", "data": {}}
		action = "enable" if state == "on" else "disable"
		_ps(f'netsh interface set interface "{iface}" {action}')
		return {"ok": True, "speech": f"WiFi turned {state}.", "data": {"iface": iface}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not toggle WiFi: {exc}", "data": {}}


@tool("bluetooth_status", "Check if Bluetooth is on or off.", {}, permission="read")
def bluetooth_status():
	try:
		out = _ps("Get-Service -Name bthserv | Select-Object Status")
		status = "on" if "Running" in out else ("off" if "Stopped" in out else "unknown")
		return {"ok": True, "speech": f"Bluetooth is {status}.", "data": {"status": status}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not check Bluetooth: {exc}", "data": {}}


@tool("bluetooth_toggle", "Turn Bluetooth on or off.",
	{"state": {"type": "string", "enum": ["on", "off"]}}, permission="execute")
def bluetooth_toggle(state="on"):
	try:
		action = "Start-Service" if state == "on" else "Stop-Service"
		_ps(f"{action} -Name bthserv -Force")
		return {"ok": True, "speech": f"Bluetooth turned {state}.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not toggle Bluetooth: {exc}", "data": {}}


@tool("brightness_get", "Get current screen brightness (0-100).", {}, permission="read")
def brightness_get():
	try:
		out = _ps(r"(Get-WmiObject -Class WmiMonitorBrightness -Namespace root\wmi).CurrentBrightness")
		level = int(out.strip())
		return {"ok": True, "speech": f"Brightness is {level}%.", "data": {"brightness": level}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not get brightness: {exc}", "data": {}}


@tool("brightness_set", "Set screen brightness (0-100).",
	{"level": {"type": "integer", "minimum": 0, "maximum": 100}}, permission="execute")
def brightness_set(level=50):
	try:
		level = max(0, min(100, int(level)))
		_ps(f"(Get-WmiObject -Class WmiMonitorBrightnessMethods -Namespace root\wmi).WmiSetBrightness(1,{level})")
		return {"ok": True, "speech": f"Brightness set to {level}%.", "data": {"brightness": level}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not set brightness: {exc}", "data": {}}


@tool("clipboard_set", "Copy text to the clipboard.",
	{"text": {"type": "string"}}, permission="execute")
def clipboard_set(text=""):
	try:
		import pyperclip
		pyperclip.copy(text)
		return {"ok": True, "speech": f"Copied {len(text)} characters.", "data": {"length": len(text)}}
	except ImportError:
		try:
			escaped = text.replace("'", "''")
			_ps(f"Set-Clipboard -Value '{escaped}'")
			return {"ok": True, "speech": f"Copied {len(text)} characters.", "data": {"length": len(text)}}
		except Exception as exc:
			return {"ok": False, "speech": f"Could not set clipboard: {exc}", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not set clipboard: {exc}", "data": {}}


@tool("clipboard_history", "Read current clipboard content.", {}, permission="read")
def clipboard_history():
	try:
		import pyperclip
		text = pyperclip.paste()
		if text:
			preview = text[:200] + ("..." if len(text) > 200 else "")
			return {"ok": True, "speech": f"Clipboard: {preview}", "data": {"text": text[:500]}}
		return {"ok": True, "speech": "Clipboard is empty.", "data": {}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not read clipboard: {exc}", "data": {}}


@tool("set_alarm", "Set an alarm. when: 'in 10 minutes', 'at 9pm', 'tomorrow at 7am'.",
	{"label": {"type": "string", "default": "Alarm"}, "when": {"type": "string"}}, permission="read")
def set_alarm(label="Alarm", when=""):
	try:
		from ..timeparse import parse_when
		due = parse_when(f"{label} {when}" if when else label)
		if not due:
			return {"ok": False, "speech": f"Could not parse when. Try 'in 10 minutes' or 'at 9pm'.", "data": {}}
		from .. import db
		rid = db.reminder_add(f"ALARM: {label}", due.timestamp())
		return {"ok": True, "speech": f"Alarm set: {label} for {due.strftime('%H:%M')}.",
				"data": {"reminder_id": rid, "due": due.isoformat()}}
	except Exception as exc:
		return {"ok": False, "speech": f"Could not set alarm: {exc}", "data": {}}
