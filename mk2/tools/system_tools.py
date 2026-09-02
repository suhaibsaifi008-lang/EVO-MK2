"""System tools: apps, shell (permissioned), volume, screenshot, screen read."""
import os
import shutil
from pathlib import Path

from .. import db as _db
from . import tool
import subprocess
import threading
import time


def _run_ps(script: str, timeout: int = 15) -> str:
    for attempt in range(2):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip()[:200] or "powershell failed")
            return (r.stdout or "").strip()
        except subprocess.TimeoutExpired:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise RuntimeError(f"PowerShell command timed out after {timeout}s")
        except Exception:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    return ""


APP_ALIASES = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe",
    "files": "explorer.exe", "cmd": "cmd.exe", "terminal": "wt.exe",
    "task manager": "taskmgr.exe", "control panel": "control.exe",
    "settings": "ms-settings:", "chrome": "chrome.exe", "edge": "msedge.exe",
    "firefox": "firefox.exe", "spotify": "spotify.exe", "brave": "brave.exe",
}

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "maps": "https://maps.google.com",
    "github": "https://github.com", "reddit": "https://www.reddit.com",
    "wikipedia": "https://en.wikipedia.org", "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com", "netflix": "https://www.netflix.com",
    "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    "linkedin": "https://www.linkedin.com", "twitch": "https://www.twitch.tv",
    "stack overflow": "https://stackoverflow.com",
    "brave search": "https://search.brave.com",
}

_apps_cache = {"items": None, "ts": 0.0}


def _installed_apps() -> list[str]:
    """All Start-Menu app names (user+system), cached 10 min. General by
    design: anything the user installs becomes openable automatically."""
    import time as _t
    from pathlib import Path

    if _apps_cache["items"] and _t.time() - _apps_cache["ts"] < 600:
        return _apps_cache["items"]
    names = set()
    bases = []
    for env in ("APPDATA", "PROGRAMDATA"):
        b = os.environ.get(env)
        if b:
            bases.append(Path(b) / r"Microsoft\Windows\Start Menu\Programs")
    for base in bases:
        if base.exists():
            for p in base.rglob("*.lnk"):
                stem = p.stem.strip()
                if 2 <= len(stem) <= 40 and not stem.startswith("{"):
                    names.add(stem)
    _apps_cache["items"] = sorted(names)
    _apps_cache["ts"] = _t.time()
    return _apps_cache["items"]


def _find_lnk(name: str) -> str | None:
    low = name.lower()
    for env in ("APPDATA", "PROGRAMDATA"):
        b = os.environ.get(env)
        if not b:
            continue
        base = Path(b) / r"Microsoft\Windows\Start Menu\Programs"
        if not base.exists():
            continue
        for p in base.rglob("*.lnk"):
            if p.stem.lower() == low or p.stem.lower().startswith(low):
                return str(p)
    return None


def _fuzzy(target: str, choices: list[str], cutoff: float = 0.72):
    from difflib import get_close_matches

    hits = get_close_matches(target.lower(), [c.lower() for c in choices], n=1, cutoff=cutoff)
    if hits:
        for c in choices:
            if c.lower() == hits[0]:
                return c
    return None


@tool("open_app", "Open ANY app, site, or URL by name. Handles typos; unknown names fall back to a web search.",
      {"target": {"type": "string"}}, permission="execute")
def open_app(target: str) -> dict:
    import difflib
    import webbrowser

    raw = target.strip()
    t = raw.lower()

    # 1) URL-ish → security gate, then browser
    url = raw if raw.startswith(("http://", "https://")) else None
    if url is None and "." in t and " " not in t and len(t.rsplit(".", 1)[-1]) <= 4:
        url = f"https://{raw}"
    if url:
        from ..security import gate

        allow, note = gate(url)
        if not allow:
            return {"ok": False,
                    "speech": (f"I'm not opening that link - it looks "
                               f"dangerous {note}. If you trust it, open it "
                               "manually and I'll stand by."),
                    "data": {"blocked": True}}
        webbrowser.open(url)
        speech = f"Opening {url}. {note}".strip()
        return {"ok": True, "speech": speech, "data": {"url": url}}

    # 2) exact alias / site / installed-app name
    alias = APP_ALIASES.get(t)
    if alias:
        if alias.endswith(":"):
            os.startfile(alias)  # noqa: S606
            return {"ok": True, "speech": f"Opening {t}.", "data": {}}
        resolved = shutil.which(alias) or shutil.which(f"{alias}.exe")
        if resolved:
            os.startfile(resolved)  # noqa: S606
            return {"ok": True, "speech": f"Opening {t}.", "data": {}}
    if t in SITES:
        webbrowser.open(SITES[t])
        return {"ok": True, "speech": f"Opening {t} in your browser.", "data": {}}

    # 3) fuzzy match across aliases + sites + every installed Start-Menu app
    candidates = list(APP_ALIASES.keys()) + list(SITES.keys()) + _installed_apps()
    lower_to_orig = {}
    for c in candidates:
        lower_to_orig.setdefault(c.lower(), c)
    match = None
    try:
        close = difflib.get_close_matches(t.lower(), list(lower_to_orig), n=1, cutoff=0.72)
        if close:
            match = lower_to_orig[close[0]]
    except Exception:
        match = None

    if match:
        alias = APP_ALIASES.get(match)
        if alias:
            resolved = shutil.which(alias) or shutil.which(f"{alias}.exe")
            if resolved:
                os.startfile(resolved)  # noqa: S606
                return {"ok": True, "speech": f"Opening {match}.", "data": {"matched": t}}
        if match in SITES:
            webbrowser.open(SITES[match])
            return {"ok": True, "speech": f"Opening {match} in your browser.", "data": {"matched": t}}
        lnk = _find_lnk(match)
        if lnk:
            os.startfile(lnk)  # noqa: S606
            return {"ok": True, "speech": f"Opening {match}.", "data": {}}
        exe = shutil.which(match) or shutil.which(f"{match}.exe")
        if exe:
            os.startfile(exe)  # noqa: S606
            return {"ok": True, "speech": f"Opening {match}.", "data": {}}

    # 4) Start-Menu direct hit (exact or prefix)
    lnk = _find_lnk(raw)
    if lnk:
        os.startfile(lnk)  # noqa: S606
        display = Path(lnk).stem
        return {"ok": True, "speech": f"Opening {display}.", "data": {}}

    # 5) Unknown → search the web instead of failing. Never crash.
    from ..config import search_url

    search_page = search_url(raw)
    webbrowser.open(search_page)
    note = f" (matched '{match}')" if match else ""
    return {"ok": True,
            "speech": f"I couldn't find an app called '{raw}'{note}, so I searched the web instead.",
            "data": {"searched": True, "url": search_page}}


@tool("close_app", "Close app windows whose title contains the target.",
      {"target": {"type": "string"}}, permission="execute")
def close_app(target: str) -> dict:
    import re as _re
    import base64
    clean = (target or "").strip()
    if not _re.fullmatch(r'[\w\s.\-]+', clean):
        return {"ok": False, "speech": f"Invalid app target: '{target}'.", "data": {}}
    b64_target = base64.b64encode(clean.encode("utf-8")).decode("ascii")
    n = _run_ps(
        f"$t = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64_target}')); "
        "(Get-Process | Where-Object {$_.MainWindowTitle -ne ''} | "
        "Where-Object { $_.MainWindowTitle -like \"*$t*\" } | "
        "ForEach-Object { $_.CloseMainWindow() } | Measure-Object).Count"
    )
    count = int(n or 0)
    if count == 0:
        return {"ok": False, "speech": f"No window matching '{target}'.", "data": {}}
    return {"ok": True, "speech": f"Closed {count} window(s).", "data": {"closed": count}}


@tool("shell_run", "Run a PowerShell command and return its output.",
      {"command": {"type": "string"}, "timeout": {"type": "integer"}}, permission="execute")
def shell_run(command: str, timeout: int = 20) -> dict:
    cmd_low = (command or "").lower()
    blocked = (
        "format ", "format.", "rmdir /s", "del /f /s", "del /s",
        "shutdown", "restart-computer", "stop-computer",
        "rm -rf", "rm -r /", "remove-item -recurse",
        "net user", "net localgroup",
        "reg delete", "reg add",
        "bcdedit", "diskpart",
        "taskkill /f /im lsass", "taskkill /f /im csrss",
        "invoke-expression", "iex ", "iex(", "downloadstring", "downloadfile",
        "-encodedcommand", "-enc ", "certutil", "bitsadmin", "curl -o", "wget -o",
        "powershell -e", "pwsh -e", "start-bitstransfer",
    )
    if any(d in cmd_low for d in blocked):
        return {"ok": False, "speech": "Command blocked: destructive or suspicious system command detected.", "data": {}}
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


@tool("web_search", "Search the web. Returns top results PLUS an excerpt of the best page - read it and answer in your own words.",
      {"query": {"type": "string"}}, permission="read")
def web_search(query: str) -> dict:
    from .web_tools import ddg_results, fetch_page_text
    rows = ddg_results(query, max_results=5)
    if not rows:
        return {"ok": False,
                "speech": "No results (web may be unreachable).",
                "data": {"results": []}}
    # Give the brain real material to synthesize from, not just titles.
    excerpt = ""
    for r in rows[:2]:
        try:
            text = fetch_page_text(r["url"], max_chars=1200)
            if len(text) > 200:
                excerpt = f"{r['title']}: {text}"
                break
        except Exception:
            continue
    # Phase 7.6: browsed pages become permanent RAG knowledge automatically
    try:
        if excerpt:
            from .. import rag as rag_mod

            url = next(r["url"] for r in rows)
            pieces = rag_mod._chunks(excerpt)
            blobs = rag_mod._embed_batch(pieces)
            _db.chunk_delete_source(url)
            for i, (piece, blob) in enumerate(zip(pieces, blobs)):
                _db.chunk_add(url, i, piece, blob)
    except Exception:
        pass
    speech = "; ".join(r["title"] for r in rows[:3])
    return {"ok": True, "speech": f"Found: {speech}",
            "data": {"results": rows,
                     "excerpt": excerpt or "(pages unreadable)"}}


_clipboard_ring: list[dict] = []
_clip_lock = threading.Lock()


def _push_clip(text: str) -> None:
    if not text:
        return
    with _clip_lock:
        if not _clipboard_ring or _clipboard_ring[-1]["text"] != text:
            _clipboard_ring.append({"text": text, "ts": time.time()})
            if len(_clipboard_ring) > 50:
                del _clipboard_ring[0]


@tool("clipboard_set", "Copy text to the clipboard and record to history.",
      {"text": {"type": "string"}}, permission="execute")
def clipboard_set(text: str = "") -> dict:
    try:
        import pyperclip
        pyperclip.copy(text)
        _push_clip(text)
        return {"ok": True, "speech": f"Copied {len(text)} characters.", "data": {"length": len(text)}}
    except ImportError:
        try:
            import subprocess
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                input=text, capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            _push_clip(text)
            return {"ok": True, "speech": f"Copied {len(text)} characters.", "data": {"length": len(text)}}
        except Exception as exc:
            return {"ok": False, "speech": f"Could not set clipboard: {exc}", "data": {}}


@tool("clipboard_history", "Read recent clipboard history (last 50 items).",
      {"limit": {"type": "integer", "default": 10}}, permission="read")
def clipboard_history(limit: int = 10) -> dict:
    try:
        import pyperclip
        cur = pyperclip.paste()
        if cur:
            _push_clip(cur)
    except Exception:
        pass
    with _clip_lock:
        items = list(reversed(_clipboard_ring))[:max(1, limit)]
    if not items:
        return {"ok": True, "speech": "Clipboard history is empty.", "data": {"items": []}}
    speech = f"{len(items)} items in clipboard history. Most recent: {items[0]['text'][:80]}"
    return {"ok": True, "speech": speech, "data": {"items": [it["text"][:300] for it in items]}}


@tool("clipboard_recall", "Recall an item from clipboard history by index (0=latest) or search query.",
      {"query": {"type": "string", "description": "Index number or search query"}}, permission="execute")
def clipboard_recall(query: str = "") -> dict:
    with _clip_lock:
        if not _clipboard_ring:
            return {"ok": False, "speech": "Clipboard history is empty.", "data": {}}
        target = None
        try:
            idx = int(str(query).strip())
            items = list(reversed(_clipboard_ring))
            if 0 <= idx < len(items):
                target = items[idx]["text"]
        except ValueError:
            q = str(query).lower()
            for it in reversed(_clipboard_ring):
                if q in it["text"].lower():
                    target = it["text"]
                    break
    if not target:
        return {"ok": False, "speech": f"No clipboard item matching '{query}' found.", "data": {}}
    return clipboard_set(target)

