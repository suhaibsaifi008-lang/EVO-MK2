"""System tools: apps, shell (permissioned), volume, screenshot, screen read."""
import os
import shutil

from . import tool
import subprocess


def _run_ps(script: str, timeout: int = 15) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200] or "powershell failed")
    return (r.stdout or "").strip()


APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe",
    "files": "explorer.exe", "cmd": "cmd.exe", "terminal": "wt.exe",
    "task manager": "taskmgr.exe", "control panel": "control.exe",
    "settings": "ms-settings:", "chrome": "chrome.exe", "edge": "msedge.exe",
    "firefox": "firefox.exe", "spotify": "spotify.exe",
}

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "maps": "https://maps.google.com",
    "github": "https://github.com", "reddit": "https://www.reddit.com",
    "wikipedia": "https://en.wikipedia.org", "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com", "netflix": "https://www.netflix.com",
}


@tool("open_app", "Open an app, known site, or URL by name.",
      {"target": {"type": "string"}}, permission="execute")
def open_app(target: str) -> dict:
    import webbrowser

    t = target.strip().lower()
    alias = APP_ALIASES.get(t)
    if alias:
        resolved = shutil.which(alias) or shutil.which(f"{alias}.exe")
        if t.endswith(":"):
            os.startfile(t)  # noqa: S606
            return {"ok": True, "speech": f"Opening {t.rstrip(':')}.", "data": {}}
        if not resolved:
            raise FileNotFoundError(f"{alias} not on PATH")
        os.startfile(resolved)  # noqa: S606
        return {"ok": True, "speech": f"Opening {t}.", "data": {}}
    if t in SITES:
        webbrowser.open(SITES[t])
        return {"ok": True, "speech": f"Opening {t} in your browser.", "data": {}}
    url = target if target.startswith(("http://", "https://")) else None
    if url is None and "." in t and " " not in t and len(t.split(".")[-1]) <= 4:
        url = f"https://{target}"
    if url:
        webbrowser.open(url)
        return {"ok": True, "speech": "Opening that site.", "data": {"url": url}}
    # last resort: let Windows resolve the name (Start Menu / Store)
    os.startfile(target)  # noqa: S606
    return {"ok": True, "speech": f"Opening {target}.", "data": {}}


@tool("close_app", "Close app windows whose title contains the target.",
      {"target": {"type": "string"}}, permission="execute")
def close_app(target: str) -> dict:
    n = _run_ps(
        "(Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
        f"Where-Object {{ $_.MainWindowTitle -like '*{target}*' }} | "
        "ForEach-Object { $_.CloseMainWindow() } | Measure-Object).Count"
    )
    count = int(n or 0)
    if count == 0:
        return {"ok": False, "speech": f"No window matching '{target}'.", "data": {}}
    return {"ok": True, "speech": f"Closed {count} window(s).", "data": {"closed": count}}


@tool("shell_run", "Run a PowerShell command and return its output.",
      {"command": {"type": "string"}, "timeout": {"type": "integer"}}, permission="execute")
def shell_run(command: str, timeout: int = 20) -> dict:
    out = _run_ps(command, timeout=max(5, min(int(timeout), 120)))
    return {"ok": True, "speech": out[:300] or "(no output)", "data": {"output": out[:4000]}}


@tool("volume", "Volume up/down/mute.", {"action": {"type": "string"}}, permission="read")
def volume(action: str) -> dict:
    key = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}.get(action.lower())
    if not key:
        raise ValueError("action must be up|down|mute")
    ps = (
        "Add-Type -Namespace W -Name K -MemberDefinition "
        "'[DllImport(\"user32.dll\")] public static extern void keybd_event(byte k, byte s, uint f, int e);';"
        f"[W.K]::keybd_event(0x{key:X},0,0,0);[W.K]::keybd_event(0x{key:X},0,2,0)"
    )
    _run_ps(ps, timeout=10)
    return {"ok": True, "speech": f"Volume {action}.", "data": {}}


@tool("web_search", "Search DuckDuckGo and return top result titles+links.",
      {"query": {"type": "string"}}, permission="read")
def web_search(query: str) -> dict:
    import urllib.parse

    from .web_tools import ddg_results
    rows = ddg_results(query, max_results=5)
    speech = "; ".join(r["title"] for r in rows[:3]) or "No results."
    return {"ok": bool(rows), "speech": f"Top results: {speech}", "data": {"results": rows}}

