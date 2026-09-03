"""Browser hands: a gated Playwright session EVO can drive.

Design rules (deliberate, keep them):
- SEPARATE persistent profile (data/browser_profile) — never the user's
  personal Chrome identity. Logins live here, isolated.
- Domain ALLOWLIST (EVO_BROWSER_ALLOW) — navigation to other hosts is
  refused, spoken clearly, and audited.
- Visible window by default (EVO_BROWSER_HEADLESS=0) — you watch it work.
- Everything goes through the normal @tool registry -> permission=execute,
  immutable audit ledger, structured {ok,speech,data} results.
- Targeting uses accessible roles/names/text (not pixel coordinates), with
  AI vision available separately via screen_read.
"""
import os
import threading
import time
from urllib.parse import urlparse

from ..config import DATA
from . import tool

PROFILE_DIR = DATA / "browser_profile"
SCREENSHOT_DIR = DATA / "vision"

_lock = threading.RLock()
_sess: dict = {"pw": None, "ctx": None, "page": None}


def _allowlist() -> list[str]:
    raw = os.environ.get(
        "EVO_BROWSER_ALLOW",
        "canva.com,youtube.com,www.youtube.com,m.youtube.com,"
        "google.com,www.google.com")
    return [d.strip().lower() for d in raw.split(",") if d.strip()]


def _headless() -> bool:
    return os.environ.get("EVO_BROWSER_HEADLESS", "0") == "1"


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().removeprefix("www.")
    for entry in _allowlist():
        e = entry.removeprefix("www.")
        if host == e or host.endswith("." + e):
            return True
    return False


def _nav_allowed(url: str) -> bool:
    u = urlparse(url)
    if u.scheme in ("file", "data"):
        return False  # H15: block file:// and data:// schemes
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower()
    return host in ("127.0.0.1", "localhost") or _host_allowed(host)


def _ensure():
    """Launch/reuse one persistent context. Serialized: Playwright's sync
    API is not thread-safe."""
    with _lock:
        if _sess["page"] is not None:
            try:
                _ = _sess["page"].url  # liveness probe
                return _sess["page"]
            except Exception:
                pass
            _shutdown()
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _sess["pw"] = sync_playwright().start()
        _sess["ctx"] = _sess["pw"].chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=_headless(),
            args=["--start-maximized"],
            viewport=None,
        )
        page = _sess["ctx"].pages[0] if _sess["ctx"].pages \
            else _sess["ctx"].new_page()
        page.set_default_timeout(15000)
        _sess["page"] = page
        return page


def _shutdown() -> None:
    with _lock:
        try:
            if _sess["ctx"]:
                _sess["ctx"].close()
        except Exception:
            pass
        try:
            if _sess["pw"]:
                _sess["pw"].stop()
        except Exception:
            pass
        _sess.update({"pw": None, "ctx": None, "page": None})


@tool("browser_open",
      "Open a URL in EVO's own visible browser window. Use for sites you "
      "must interact with afterwards (Canva, YouTube studio, web apps). "
      "Only allow-listed domains are permitted.",
      {"url": {"type": "string"}}, permission="execute", long_running=True)
def browser_open(url: str) -> dict:
    url = (url or "").strip()
    if not _nav_allowed(url):
        host = urlparse(url).hostname or "?"
        return {
            "ok": False,
            "speech": f"'{host}' is not on my browser allow-list "
                      f"({', '.join(_allowlist())}). Add it via "
                      f"EVO_BROWSER_ALLOW if you want me to go there.",
            "data": {"allowed": False},
        }
    try:
        page = _ensure()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        title = (page.title() or "").strip()
        return {"ok": True, "speech": f"Opened {title or url}.",
                "data": {"url": page.url, "title": title}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "speech": f"Couldn't open that page: "
                                       f"{str(exc)[:140]}", "data": {}}


@tool("browser_read",
      "Read the current browser page: title, URL and visible text. Use "
      "before acting so you know what is on screen.",
      {}, permission="read", long_running=True)
def browser_read() -> dict:
    try:
        page = _ensure()
        title = (page.title() or "").strip()
        body = page.inner_text("body")
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        preview = " | ".join(lines)[:1200]
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shot = SCREENSHOT_DIR / f"browser_{int(time.time())}.png"
        try:
            page.screenshot(path=str(shot))
        except Exception:
            shot = None
        from ..firewall import wrap_untrusted_data, scan_prompt_injection
        is_inj, rule, _ = scan_prompt_injection(preview)
        safe_preview = wrap_untrusted_data(preview, source=page.url)

        return {"ok": True,
                "speech": f"On '{title}': {preview[:200]}",
                "data": {"url": page.url, "title": title,
                         "text": safe_preview, "screenshot": str(shot or ""),
                         "injection_detected": is_inj}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "speech": f"Browser read failed: "
                                       f"{str(exc)[:140]}", "data": {}}


def _click_targets(page, target: str):
    """Yield locators best-first by accessible name/text."""
    yield page.get_by_role("button", name=target)
    yield page.get_by_role("link", name=target)
    yield page.get_by_role("tab", name=target)
    yield page.get_by_role("menuitem", name=target)
    yield page.get_by_text(target, exact=False).first
    yield page.locator(f"[aria-label='{target}']").first


def _field_targets(page, target: str):
    yield page.get_by_label(target)
    yield page.get_by_placeholder(target)
    yield page.get_by_role("textbox", name=target)
    yield page.locator(f"[aria-label='{target}']").first
    yield page.get_by_text(target, exact=False).first


@tool("browser_act",
      "Act on the current browser page. action='click' target=<button/link "
      "text>; action='type' target=<field label/placeholder> value=<text> "
      "(set submit=true to press Enter); action='press' value=<key like "
      "Enter/Escape>; action='scroll' value=down|up|top|bottom.",
      {"action": {"type": "string"}, "target": {"type": "string"},
       "value": {"type": "string"}, "submit": {"type": "boolean"}},
      permission="execute", long_running=True)
def browser_act(action: str, target: str = "", value: str = "",
                submit: bool = False) -> dict:
    action = (action or "").lower().strip()
    try:
        page = _ensure()
        if action == "click":
            if not target:
                return {"ok": False, "speech": "Click needs a target name.",
                        "data": {}}
            errors = []
            for loc in _click_targets(page, target):
                try:
                    loc.click(timeout=3500)
                    time.sleep(0.6)          # let the SPA react
                    return {"ok": True,
                            "speech": f"Clicked '{target}'.",
                            "data": {"url": page.url}}
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc)[:60])
            return {"ok": False,
                    "speech": f"Couldn't find anything clickable called "
                              f"'{target}'. Try browser_read first.",
                    "data": {}}

        if action == "type":
            if not target:
                return {"ok": False, "speech": "Type needs a field target.",
                        "data": {}}
            for loc in _field_targets(page, target):
                try:
                    loc.fill(value, timeout=3500)
                    if submit:
                        loc.press("Enter")
                        time.sleep(0.8)
                    return {"ok": True,
                            "speech": f"Typed into '{target}'"
                                      + (" and submitted." if submit else "."),
                            "data": {"url": page.url}}
                except Exception:  # noqa: BLE001
                    continue
            return {"ok": False,
                    "speech": f"No field called '{target}' on this page.",
                    "data": {}}

        if action == "press":
            page.keyboard.press(value or "Enter")
            return {"ok": True, "speech": f"Pressed {value or 'Enter'}.",
                    "data": {}}

        if action == "scroll":
            amount = {"down": 900, "up": -900}.get((value or "down").lower())
            if amount is None:
                page.keyboard.press("Home" if value == "top" else "End")
            else:
                page.mouse.wheel(0, amount)
            return {"ok": True, "speech": "Scrolled.", "data": {}}

        return {"ok": False,
                "speech": "Unknown browser action; use click, type, press "
                          "or scroll.", "data": {}}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "speech": f"Browser action failed: "
                                       f"{str(exc)[:140]}", "data": {}}


@tool("browser_close", "Close EVO's browser window and end the session.",
      {}, permission="execute")
def browser_close() -> dict:
    _shutdown()
    return {"ok": True, "speech": "Browser closed.", "data": {}}
