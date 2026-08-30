"""Generic Autonomous Web Agent for EVO MK2 (JARVIS Phase 4).

Navigates and performs tasks on ANY arbitrary website using Playwright DOM analysis
and LLM-guided vision/action reasoning with moral checkpoints at each step.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from .. import llm
from ..audit import get_audit_logger
from ..browser_agent import get_browser_agent
from ..consent import get_consent_manager
from ..ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.platforms.web_agent")


class WebAgent:
    """Autonomous agent capable of navigating and executing tasks on any website."""

    def __init__(self):
        self.browser = get_browser_agent()
        self.ethics = get_moral_engine()
        self.consent = get_consent_manager()
        self.audit = get_audit_logger()

    def interact(self, url: str, task: str, max_steps: int = 5) -> MoralVerdict:
        """Navigate to an arbitrary URL and execute an open-ended task with safety checks."""
        action = {"action": "web_agent_task", "url": url, "task": task}

        # 1. Moral pre-check
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        # 2. Consent check
        if not self.consent.has_consent("browser_navigate"):
            return MoralVerdict.caution("Web agent requires browsing consent.", action=action)

        # 3. Navigate
        nav_res = self.browser.navigate(url)
        if nav_res.verdict != "safe":
            return nav_res

        step_history: list[str] = []

        for step in range(max_steps):
            if not self.browser.page:
                break

            current_url = getattr(self.browser.page, "url", url)
            page_text = "\n".join(self.browser.extract_text("h1, h2, h3, button, a, input, p")[:30])

            prompt = (
                f"You are EVO's autonomous web agent executing this task: \"{task}\"\n"
                f"Current URL: {current_url}\n"
                f"Current Visible DOM Elements:\n{page_text[:1500]}\n"
                f"Previous steps taken: {step_history}\n\n"
                "Decide the next action to advance the task.\n"
                'Return ONLY JSON: {"action": "click"|"type"|"wait"|"finish", "selector": "<css_selector_or_text>", "value": "<text_to_type_if_any>", "reason": "<why>"}'
            )

            try:
                plan_raw = llm.chat([
                    {"role": "system", "content": "You are a precise web automation planner."},
                    {"role": "user", "content": prompt},
                ], role="fast", temperature=0.1)

                clean = plan_raw.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
                step_plan = json.loads(clean.strip())
            except Exception as exc:
                log.warning("WebAgent action planning error: %s", exc)
                break

            act_type = step_plan.get("action")
            selector = step_plan.get("selector", "")
            val = step_plan.get("value", "")

            # Step-level moral check
            step_verdict = self.ethics.evaluate({"action": f"web_{act_type}", "selector": selector, "url": current_url})
            if step_verdict.verdict == "block":
                return step_verdict

            if act_type == "finish":
                step_history.append("Task concluded by planner.")
                break
            elif act_type == "click" and selector:
                self.browser.click(selector)
                step_history.append(f"Clicked {selector}")
            elif act_type == "type" and selector:
                self.browser.type_text(selector, val)
                step_history.append(f"Typed '{val}' into {selector}")
            elif act_type == "wait":
                time.sleep(2)
                step_history.append("Waited 2 seconds.")

            time.sleep(1)

        result_summary = f"Executed {len(step_history)} steps on {url}: " + "; ".join(step_history)
        self.audit.log_action(action, v, {"ok": True, "steps": step_history})
        return MoralVerdict.safe(result_summary, action={"url": url, "task": task, "steps": step_history})


_global_web_agent: Optional[WebAgent] = None


def get_web_agent() -> WebAgent:
    global _global_web_agent
    if _global_web_agent is None:
        _global_web_agent = WebAgent()
    return _global_web_agent
