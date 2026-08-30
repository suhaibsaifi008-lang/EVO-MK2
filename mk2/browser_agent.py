"""Persistent Browser Agent for EVO MK2 (JARVIS Phase 2).

Manages a persistent Chromium browser context with saved login cookies,
session states, anti-detection measures, and pre-execution moral guardrails.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .audit import get_audit_logger
from .browser_selectors import get_platform_config
from .config import DATA
from .consent import get_consent_manager
from .credential_vault import get_credential_vault
from .ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.browser_agent")

DEFAULT_PROFILE_DIR = DATA / "browser_profile"


class BrowserAgent:
    """Persistent Playwright browser agent with session memory and moral checks."""

    def __init__(self, profile_dir: Optional[Path] = None, headless: Optional[bool] = None):
        self.profile_dir = profile_dir or DEFAULT_PROFILE_DIR
        self.headless = headless if headless is not None else (os.environ.get("EVO_BROWSER_HEADLESS", "1") == "1")
        self.playwright = None
        self.context = None
        self.page = None
        self.sessions: dict[str, dict[str, Any]] = {}
        self.vault = get_credential_vault()
        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.audit = get_audit_logger()

    def start(self) -> bool:
        """Launch or attach to persistent browser context."""
        if self.context and self.page:
            return True
        try:
            from playwright.sync_api import sync_playwright

            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.playwright = sync_playwright().start()
            self.context = self.playwright.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                locale="en-US",
                args=["--disable-blink-features=AutomationControlled"],
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            log.info("BrowserAgent persistent context launched at %s", self.profile_dir)
            return True
        except Exception as exc:
            log.warning("BrowserAgent failed to launch: %s", exc)
            return False

    def stop(self) -> None:
        """Close browser context and save session state."""
        try:
            if self.context:
                self.context.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as exc:
            log.debug("BrowserAgent stop cleanup: %s", exc)
        finally:
            self.context = None
            self.playwright = None
            self.page = None
            log.info("BrowserAgent stopped.")

    def navigate(self, url: str) -> MoralVerdict:
        """Navigate to a target URL with moral and consent checks."""
        # 1. Consent check
        if not self.consent.has_consent("browser_navigate"):
            return MoralVerdict.caution("Browser navigation requires approval.", risks=["unapproved_navigation"])

        # 2. Moral evaluation
        action = {"action": "browser_navigate", "url": url}
        verdict = self.ethics.evaluate(action)
        if verdict.verdict == "block":
            self.audit.log_action(action, verdict, {"ok": False})
            return verdict

        # 3. Execution
        if not self.start():
            return MoralVerdict.caution("Could not launch browser engine.", risks=["browser_launch_failure"])

        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
            self.audit.log_action(action, verdict, {"ok": True, "url": self.page.url})
            return MoralVerdict.safe(f"Navigated to {url}", action=action)
        except Exception as exc:
            log.warning("Failed navigating to %s: %s", url, exc)
            self.audit.log_action(action, verdict, {"ok": False, "error": str(exc)})
            return MoralVerdict.caution(f"Navigation error: {exc}", risks=["navigation_timeout"])

    def click(self, selector: str) -> MoralVerdict:
        """Click an element with moral and consent checks."""
        if not self.consent.has_consent("browser_click"):
            return MoralVerdict.caution("Browser click requires execution approval.", risks=["unapproved_click"])

        action = {"action": "browser_click", "selector": selector, "url": getattr(self.page, "url", "")}
        verdict = self.ethics.evaluate(action)
        if verdict.verdict == "block":
            self.audit.log_action(action, verdict, {"ok": False})
            return verdict

        if not self.start() or not self.page:
            return MoralVerdict.caution("Browser page unavailable.")

        try:
            self.page.click(selector, timeout=10000)
            self.audit.log_action(action, verdict, {"ok": True})
            return MoralVerdict.safe(f"Clicked {selector}")
        except Exception as exc:
            self.audit.log_action(action, verdict, {"ok": False, "error": str(exc)})
            return MoralVerdict.caution(f"Click failed: {exc}", risks=["element_not_found"])

    def type_text(self, selector: str, text: str, delay: float = 0.03) -> MoralVerdict:
        """Type text into a field with human-like keystroke delays."""
        if not self.consent.has_consent("browser_type"):
            return MoralVerdict.caution("Browser typing requires execution approval.")

        action = {"action": "browser_type", "selector": selector, "chars": len(text)}
        verdict = self.ethics.evaluate(action)
        if verdict.verdict == "block":
            self.audit.log_action(action, verdict, {"ok": False})
            return verdict

        if not self.start() or not self.page:
            return MoralVerdict.caution("Browser page unavailable.")

        try:
            self.page.fill(selector, "")
            self.page.type(selector, text, delay=int(delay * 1000))
            self.audit.log_action(action, verdict, {"ok": True})
            return MoralVerdict.safe(f"Typed text into {selector}")
        except Exception as exc:
            self.audit.log_action(action, verdict, {"ok": False, "error": str(exc)})
            return MoralVerdict.caution(f"Typing failed: {exc}", risks=["type_failure"])

    def extract_text(self, selector: str) -> list[str]:
        """Extract text content from elements matching selector."""
        if not self.start() or not self.page:
            return []
        try:
            elements = self.page.query_selector_all(selector)
            return [el.inner_text().strip() for el in elements if el.inner_text().strip()]
        except Exception:
            return []

    def screenshot(self, target_path: Optional[Path] = None) -> Optional[Path]:
        """Capture screenshot of current page."""
        if not self.start() or not self.page:
            return None
        p = target_path or (DATA / f"browser_shot_{int(time.time())}.png")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(p))
            return p
        except Exception as exc:
            log.warning("Screenshot failed: %s", exc)
            return None

    def login(self, service: str) -> MoralVerdict:
        """Automated login to a service using stored credentials."""
        cfg = get_platform_config(service)
        if not cfg:
            return MoralVerdict.block(f"Unsupported platform: {service}")

        creds = self.vault.get(service)
        if not creds:
            return MoralVerdict.block(f"No credentials configured for {service}. Add them to CredentialVault.")

        login_url = cfg["login_url"]
        nav_res = self.navigate(login_url)
        if nav_res.verdict != "safe":
            return nav_res

        selectors = cfg["selectors"]
        u_sel = selectors.get("username")
        p_sel = selectors.get("password")
        s_sel = selectors.get("submit")

        if not u_sel or not p_sel or not s_sel:
            return MoralVerdict.caution(f"Incomplete selectors for {service}")

        # Check if already logged in by inspecting cookies or URL
        if self.sessions.get(service, {}).get("logged_in"):
            return MoralVerdict.safe(f"Already logged into {service}")

        try:
            username = creds.get("username") or creds.get("email")
            password = creds.get("password")
            if not username or not password:
                return MoralVerdict.block("Missing username or password in stored credentials.")

            self.type_text(u_sel, username)
            self.type_text(p_sel, password)
            self.click(s_sel)

            # Wait for network idle or URL change
            self.page.wait_for_load_state("networkidle", timeout=12000)
            self.sessions[service] = {
                "logged_in": True,
                "login_ts": time.time(),
            }
            return MoralVerdict.safe(f"Successfully logged into {service}")
        except Exception as exc:
            log.warning("Login to %s failed: %s", service, exc)
            return MoralVerdict.caution(f"Login sequence incomplete: {exc}", risks=["login_failed"])


_global_browser: Optional[BrowserAgent] = None


def get_browser_agent() -> BrowserAgent:
    global _global_browser
    if _global_browser is None:
        _global_browser = BrowserAgent()
    return _global_browser
