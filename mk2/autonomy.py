"""EVO MK2 Autonomy Engine -- Phase 8: Full Autonomous Operation.

This module makes EVO truly autonomous - capable of taking a high-level
goal like "earn money" and executing the complete path: research strategies,
break them into steps, execute each step, learn from outcomes, adapt on
failure, and chain missions together.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import config, db, llm, tools
from .bus import bus
from .tools import tool

log = logging.getLogger("mk2.autonomy")


def _log_event(subsystem: str, event: str, **kwargs):
    log.info("[%s] %s %s", subsystem, event, " ".join(f"{k}={v}" for k, v in kwargs.items()))


AUTO_DATA = config.DATA / "autonomy"
AUTO_DATA.mkdir(parents=True, exist_ok=True)

STRATEGIES_FILE = AUTO_DATA / "strategies.json"
OUTCOMES_FILE = AUTO_DATA / "outcomes.json"
GOALS_FILE = AUTO_DATA / "active_goals.json"
LEARNED_FILE = AUTO_DATA / "learned.json"

_PERMISSION_TIERS: dict[str, dict[str, Any]] = {
    "safe": {
        "description": "Read-only research, file creation, note-taking",
        "allow": {
            "fs_write", "fs_read", "deep_research", "clipboard_set", "task_start",
            "task_status", "task_stop", "task_resume", "task_retry",
            "translate", "screen_read", "remember_episode", "web_search",
            "system_info", "docs_create", "docs_append", "timer_set", "timer_list", "timer_cancel",
            "weather_now", "vault_write", "vault_read", "vault_search", "vault_list", "vault_delete",
            "todo_add", "todo_list", "todo_done", "screenshot", "tts_set_voice",
            "tts_list_voices", "tts_voices", "tts_speak", "tts_rate", "browser_open",
            "browser_read", "browser_navigate", "browser_screenshot", "youtube_summarize",
            "open_app", "volume_set", "volume_get", "rag_ingest", "rag_ask", "ensemble_ask",
            "secret_store", "secret_get", "secret_delete", "secret_list",
            "connector_add", "connector_call", "forge_skill", "forge_list", "forge_delete",
            "run_tests", "selfcheck_now", "url_check", "breach_check", "life_admin_ingest",
            "expense_summary", "subscription_audit", "set_persona", "persona_summary",
            "initiative_now", "workflow_run", "workflow_list", "workflow_create", "workflow_delete",
            "habit_approve", "habit_reject", "habit_list", "mail_draft", "mail_check", "mail_unread", "mail_send",
            "push_send", "reminder_set", "reminder_list", "reminder_cancel",
            "skill_save", "skill_list", "skill_delete", "proposal_approve", "proposal_reject",
        },
        "deny": {
            "mouse_click", "type_text", "press_key", "shell_run",
            "browser_click", "browser_type", "browser_act",
            "browser_close",
        },
    },
    "standard": {
        "description": "Safe + messaging, form filling, content posting",
        "allow": {
            "fs_write", "fs_read", "deep_research", "push_send", "browser_screenshot", "web_search",
            "docs_create", "docs_append", "browser_click", "browser_type", "browser_navigate",
            "mail_send", "mail_draft", "mail_check", "mail_unread", "vault_write", "vault_read",
            "vault_search", "vault_list", "vault_delete", "remember_episode", "screenshot",
            "system_info", "weather_now", "clipboard_set", "screen_read",
            "translate", "task_start", "task_status", "task_stop", "task_resume",
            "task_retry", "timer_set", "timer_list", "timer_cancel", "todo_add", "todo_list", "todo_done",
            "tts_set_voice", "tts_list_voices", "tts_voices", "tts_speak", "tts_rate", "browser_open",
            "browser_read", "youtube_summarize", "open_app", "volume_set", "volume_get",
            "rag_ingest", "rag_ask", "ensemble_ask", "secret_store", "secret_get", "secret_delete",
            "secret_list", "connector_add", "connector_call", "forge_skill", "forge_list", "forge_delete",
            "run_tests", "selfcheck_now", "url_check", "breach_check", "life_admin_ingest",
            "expense_summary", "subscription_audit", "set_persona", "persona_summary",
            "initiative_now", "workflow_run", "workflow_list", "workflow_create", "workflow_delete",
            "habit_approve", "habit_reject", "habit_list", "reminder_set", "reminder_list", "reminder_cancel",
            "skill_save", "skill_list", "skill_delete", "proposal_approve", "proposal_reject",
        },
        "deny": {
            "mouse_click", "type_text", "press_key", "shell_run",
            "browser_act", "browser_close",
        },
    },
    "extended": {
        "description": "Standard + account creation, platform interaction",
        "allow": {
            "type_text", "press_key", "mouse_click", "shell_run",
            "browser_navigate", "browser_click", "browser_type", "browser_screenshot",
            "browser_open", "browser_act", "browser_read", "browser_close",
        },
        "deny": set(),
    },
    "full": {
        "description": "All capabilities",
        "allow": set(),
        "deny": set(),
    },
}


def get_permission_level() -> str:
    return os.environ.get("EVO_AUTONOMY_LEVEL", "safe").strip().lower()


def is_allowed(tool_name: str, permission: str = "", context: dict | None = None) -> bool:
    level = get_permission_level()
    tier = _PERMISSION_TIERS.get(level, _PERMISSION_TIERS["safe"])

    # Explicit deny lists take absolute precedence
    if tier.get("deny") and tool_name in tier["deny"]:
        return False

    if context and context.get("surface") == "voice":
        voice_deny = {"shell_run", "mouse_click", "type_text", "press_key", "process_kill", "fs_delete"}
        if tool_name in voice_deny:
            return False

    if context and context.get("quiet_hours"):
        dangerous = {"shell_run", "fs_delete", "mail_send", "stripe_invoice"}
        if tool_name in dangerous:
            return False

    # Critical capability semantics: high blast-radius tools can never bypass tier checks
    critical_capabilities = {
        "shell_run", "process_kill", "stripe_invoice", "delete_file",
        "fs_delete", "mail_send", "autonomy_permission", "pc_control"
    }
    if tool_name in critical_capabilities and level != "full":
        return False

    # Full autonomy tier allows all non-denied tools
    if level == "full":
        return True

    # Enforce verified capability allow-list for non-full tiers
    if tier.get("allow"):
        return tool_name in tier["allow"]

    return False


@dataclass
class SubTask:
    id: str = ""
    description: str = ""
    tool_hint: str = ""
    depends_on: list = field(default_factory=list)
    status: str = "pending"
    result: str = ""
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class Mission:
    id: str = ""
    goal: str = ""
    subtasks: list = field(default_factory=list)
    status: str = "planning"
    current_step: int = 0
    strategy_used: str = ""
    outcome: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    context: dict = field(default_factory=dict)
    session_state: dict = field(default_factory=lambda: {
        "browser_session_id": "",
        "form_progress": {},
        "login_state": False,
        "checkout_step": "",
    })


def load_learned_strategies() -> list[dict]:
    if LEARNED_FILE.exists():
        try:
            return json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def record_learned_strategy(goal: str, subtasks: list, success: bool, duration_s: float = 0.0, strategy: str = "") -> None:
    try:
        data = load_learned_strategies()
        tool_seq = [getattr(st, "tool_hint", "") for st in subtasks]
        data.append({
            "ts": time.time(),
            "goal": goal,
            "strategy": strategy,
            "tool_sequence": tool_seq,
            "success": success,
            "duration_s": round(duration_s, 2),
        })
        LEARNED_FILE.write_text(json.dumps(data[-300:], indent=2), encoding="utf-8")
    except Exception:
        pass


_DECOMPOSER_PROMPT = (
    "You are a goal execution planner. Break the user's goal into 3-8 concrete, "
    "executable sub-tasks. Each sub-task must be completable by an AI agent with "
    "web search, file creation, web browsing, and messaging tools. Order them logically. "
    "Make them SPECIFIC and ACTIONABLE. For money-making goals include: market research, "
    "platform selection, profile/account setup, outreach, delivery. For learning goals include: "
    "resource finding, study plan, practice, projects. Respond ONLY with valid JSON: "
    '{"strategy": "<approach>", "subtasks": [{"id": "s1", "description": "<task>", '
    '"tool_hint": "<tool>", "depends_on": []}]}'
)


def _decompose(goal: str, context: dict | None = None) -> Mission:
    ctx_str = json.dumps(context or {}, default=str)
    # Strategy learning injection
    learned = [s for s in load_learned_strategies() if s.get("success")]
    matching = [s for s in learned if any(w in s.get("goal", "").lower() for w in goal.lower().split() if len(w) > 4)]
    learned_prompt = ""
    if matching:
        best = matching[-1]
        learned_prompt = f"\nPast successful strategy: {best.get('strategy', '')} using tools {best.get('tool_sequence', [])}"

    messages = [
        {"role": "system", "content": _DECOMPOSER_PROMPT},
        {"role": "user", "content": f"Goal: {goal}\nContext: {ctx_str}{learned_prompt}"},
    ]
    try:
        raw = llm.chat(messages, temperature=0.3, timeout=30, role="primary")
    except Exception:
        raw = (
            '{"strategy": "execute directly", "subtasks": [{"id": "s1", "description": "'
            + goal[:100]
            + '", "tool_hint": "deep_research", "depends_on": []}]}'
        )
    try:
        data = json.loads(raw)
        strategy = data.get("strategy", "execute directly")
        subtasks = []
        for i, st in enumerate(data.get("subtasks", [])):
            subtasks.append(
                SubTask(
                    id=st.get("id", f"s{i+1}"),
                    description=st.get("description", ""),
                    tool_hint=st.get("tool_hint", "task_start"),
                    depends_on=st.get("depends_on", []),
                )
            )
        if not subtasks:
            subtasks.append(SubTask(id="s1", description=goal, tool_hint="deep_research"))
    except Exception:
        strategy = "execute directly"
        subtasks = [SubTask(id="s1", description=goal, tool_hint="deep_research")]

    return Mission(
        id=uuid.uuid4().hex[:12],
        goal=goal,
        subtasks=subtasks,
        status="planning",
        strategy_used=strategy,
        context=context or {},
    )


class StrategyLibrary:
    def __init__(self) -> None:
        self.strategies: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if STRATEGIES_FILE.exists():
            try:
                self.strategies = json.loads(STRATEGIES_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.strategies = {}

    def _save(self) -> None:
        try:
            STRATEGIES_FILE.write_text(json.dumps(self.strategies, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _match_pattern(self, goal: str) -> str:
        gl = goal.lower()
        for kw in ("earn_money", "learn_skill", "build_project", "research", "automate", "market", "write", "sell"):
            if kw.replace("_", " ") in gl:
                return kw
        return "general"

    def get_strategy(self, goal: str) -> dict | None:
        pat = self._match_pattern(goal)
        strats = self.strategies.get(pat, [])
        if not strats:
            return None
        return max(strats, key=lambda s: s.get("success_rate", 0))

    def record_outcome(self, goal: str, strategy_name: str, success: bool, details: str = "") -> None:
        pat = self._match_pattern(goal)
        if pat not in self.strategies:
            self.strategies[pat] = []
        found = None
        for s in self.strategies[pat]:
            if s.get("name") == strategy_name:
                found = s
                break
        if not found:
            found = {"name": strategy_name, "attempts": 0, "successes": 0, "success_rate": 0.0}
            self.strategies[pat].append(found)
        found["attempts"] = found.get("attempts", 0) + 1
        if success:
            found["successes"] = found.get("successes", 0) + 1
        found["success_rate"] = found["successes"] / max(1, found["attempts"])
        found["last_outcome"] = "success" if success else "failure"
        found["last_details"] = details
        self._save()


_strategy_lib: StrategyLibrary | None = None


def get_strategy_lib() -> StrategyLibrary:
    global _strategy_lib
    if _strategy_lib is None:
        _strategy_lib = StrategyLibrary()
    return _strategy_lib


class OutcomeTracker:
    def __init__(self) -> None:
        self.outcomes: list[dict] = []
        self._load()

    def _load(self) -> None:
        if OUTCOMES_FILE.exists():
            try:
                self.outcomes = json.loads(OUTCOMES_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.outcomes = []

    def _save(self) -> None:
        try:
            OUTCOMES_FILE.write_text(json.dumps(self.outcomes[-500:], indent=2), encoding="utf-8")
        except Exception:
            pass

    def record(self, mission_id: str, goal: str, subtask_id: str, tool_used: str,
               success: bool, result_summary: str = "", duration_s: float = 0) -> None:
        entry = {
            "ts": time.time(),
            "mission_id": mission_id,
            "goal": goal,
            "subtask_id": subtask_id,
            "tool": tool_used,
            "success": success,
            "result": result_summary[:200],
            "duration_s": round(duration_s, 2),
        }
        self.outcomes.append(entry)
        self._save()

    def get_best_approaches(self, goal_pattern: str, limit: int = 5) -> list[dict]:
        pat = goal_pattern.lower()
        matching = [o for o in self.outcomes if pat in o.get("goal", "").lower()]
        tool_wins: dict[str, int] = {}
        for o in matching:
            t = o.get("tool", "")
            if t:
                tool_wins[t] = tool_wins.get(t, 0) + (1 if o.get("success") else 0)
        sorted_tools = sorted(tool_wins.items(), key=lambda x: x[1], reverse=True)
        return [{"tool": t, "wins": w} for t, w in sorted_tools[:limit]]


_outcome_tracker: OutcomeTracker | None = None


def get_outcome_tracker() -> OutcomeTracker:
    global _outcome_tracker
    if _outcome_tracker is None:
        _outcome_tracker = OutcomeTracker()
    return _outcome_tracker


class BrowserAgent:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None
        self.session_state = {
            "current_url": "",
            "cookies": [],
            "form_data": {},
            "step_history": [],
        }

    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_page(self):
        if self._page:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            context = self._browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 800},
            )
            self._page = context.new_page()
            return self._page
        except Exception as exc:
            raise RuntimeError(f"Playwright not installed or failed: {exc}")

    def navigate(self, url: str) -> dict:
        try:
            from .tools.browser_tools import _nav_allowed
            if not _nav_allowed(url):
                return {"ok": False, "speech": f"Navigation blocked by security allowlist: {url}", "data": {}}
            page = self._ensure_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            text = page.evaluate("() => document.body.innerText")[:2000]
            self.session_state["current_url"] = url
            self.session_state["step_history"].append({"action": "navigate", "url": url, "title": title})
            return {"ok": True, "speech": f"Opened: {title}", "data": {"title": title, "text": text, "url": url}}
        except Exception as exc:
            return {"ok": False, "speech": f"Navigation failed: {exc}", "data": {}}

    def click(self, selector: str, description: str = "") -> dict:
        try:
            page = self._ensure_page()
            page.click(selector, timeout=10000)
            self.session_state["step_history"].append({"action": "click", "selector": selector})
            return {"ok": True, "speech": f"Clicked: {description or selector}", "data": {}}
        except Exception as exc:
            return {"ok": False, "speech": f"Click failed: {exc}", "data": {}}

    def type_text(self, selector: str, text: str, clear: bool = True) -> dict:
        try:
            page = self._ensure_page()
            if clear:
                page.fill(selector, "")
            page.type(selector, text)
            self.session_state["form_data"][selector] = text
            self.session_state["step_history"].append({"action": "type", "selector": selector, "len": len(text)})
            return {"ok": True, "speech": f"Typed {len(text)} chars", "data": {}}
        except Exception as exc:
            return {"ok": False, "speech": f"Type failed: {exc}", "data": {}}

    def screenshot(self) -> dict:
        try:
            page = self._ensure_page()
            import base64
            buf = page.screenshot(type="png")
            b64 = base64.b64encode(buf).decode("ascii")
            return {"ok": True, "speech": "Screenshot taken", "data": {"image_b64": b64}}
        except Exception as exc:
            return {"ok": False, "speech": f"Screenshot failed: {exc}", "data": {}}

    def close(self) -> None:
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None


_browser_agent: BrowserAgent | None = None


def get_browser() -> BrowserAgent:
    global _browser_agent
    if _browser_agent is None:
        _browser_agent = BrowserAgent()
    return _browser_agent


class AutonomousRunner:
    def __init__(self) -> None:
        self.missions: dict[str, Mission] = {}
        self.session_state: dict = {
            "browser_session_id": "",
            "form_progress": {},
            "login_state": False,
            "checkout_step": "",
        }
        self._lock = threading.Lock()
        self._load_state()

    def _load_state(self) -> None:
        with self._lock:
            if GOALS_FILE.exists():
                try:
                    raw = json.loads(GOALS_FILE.read_text(encoding="utf-8"))
                    self.session_state = raw.get("session_state", self.session_state)
                    b = get_browser()
                    if "browser_state" in raw and hasattr(b, "session_state"):
                        b.session_state.update(raw.get("browser_state", {}))
                    for m_data in raw.get("missions", []):
                        st_list = [
                            SubTask(
                                id=st.get("id", ""),
                                description=st.get("description", ""),
                                tool_hint=st.get("tool_hint", ""),
                                depends_on=st.get("depends_on", []),
                                status=st.get("status", "pending"),
                                result=st.get("result", ""),
                                attempts=st.get("attempts", 0),
                            )
                            for st in m_data.get("subtasks", [])
                        ]
                        m = Mission(
                            id=m_data.get("id", ""),
                            goal=m_data.get("goal", ""),
                            subtasks=st_list,
                            status=m_data.get("status", "planning"),
                            strategy_used=m_data.get("strategy", ""),
                            context=m_data.get("context", {}),
                            session_state=m_data.get("session_state", {
                                "browser_session_id": "",
                                "form_progress": {},
                                "login_state": False,
                                "checkout_step": "",
                            }),
                        )
                        self.missions[m.id] = m
                except Exception as exc:
                    log.warning("Autonomy state file %s corrupt: %s. Attempting backup restore.", GOALS_FILE, exc)
                    bak = GOALS_FILE.with_suffix(".bak")
                    if bak.exists():
                        try:
                            raw_bak = json.loads(bak.read_text(encoding="utf-8"))
                            for m_data in raw_bak.get("missions", []):
                                m_id = m_data.get("id", "")
                                if m_id:
                                    self.missions[m_id] = Mission(
                                        id=m_id,
                                        goal=m_data.get("goal", ""),
                                        subtasks=[],
                                        status=m_data.get("status", "planning"),
                                    )
                            log.info("Restored %d missions from backup", len(self.missions))
                        except Exception as b_exc:
                            log.warning("Backup restore also failed: %s", b_exc)

    def _save_state(self) -> None:
        with self._lock:
            try:
                out = {
                    "updated": time.time(),
                    "session_state": self.session_state,
                    "browser_state": getattr(get_browser(), "session_state", {}),
                    "missions": [
                        {
                            "id": m.id,
                            "goal": m.goal,
                            "status": m.status,
                            "strategy": m.strategy_used,
                            "context": m.context,
                            "session_state": m.session_state,
                            "subtasks": [
                                {
                                    "id": st.id,
                                    "description": st.description,
                                    "tool_hint": st.tool_hint,
                                    "depends_on": st.depends_on,
                                    "result": st.result,
                                    "attempts": st.attempts,
                                }
                                for st in m.subtasks
                            ],
                        }
                        for m in self.missions.values()
                    ],
                }
                GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp_file = GOALS_FILE.with_suffix(".tmp")
                tmp_file.write_text(json.dumps(out, indent=2), encoding="utf-8")
                bak_file = GOALS_FILE.with_suffix(".bak")
                if GOALS_FILE.exists():
                    try:
                        shutil.copy2(GOALS_FILE, bak_file)
                    except Exception:
                        pass
                tmp_file.replace(GOALS_FILE)
            except Exception as exc:
                log.warning("Autonomy _save_state failed: %s", exc)

    def _verify_progress(self, mission: Mission, subtask: SubTask, result: dict) -> dict:
        """Verify if a subtask's outcome genuinely advances toward the overall goal."""
        try:
            prompt = (
                f"Overall Goal: {mission.goal}\n"
                f"Completed Subtask: {subtask.description}\n"
                f"Tool Result: {str(result.get('speech', ''))[:300]}\n"
                "Did this step advance us toward the goal? "
                'Reply ONLY valid JSON: {"closer": true, "assessment": "<one brief sentence>"}'
            )
            raw = llm.chat([
                {"role": "system", "content": "You are an autonomous mission verification evaluator."},
                {"role": "user", "content": prompt}
            ], temperature=0.1, timeout=12, role="fast")
            data = json.loads(raw)
            return {"closer": bool(data.get("closer", False)), "assessment": data.get("assessment", "")}
        except Exception as exc:
            log.warning("Progress verification LLM failed: %s", exc)
            tool_ok = bool(result.get("ok", False))
            return {"closer": tool_ok, "assessment": "Heuristic fallback based on execution outcome"}

    def _replan(self, mission: Mission, failed_subtask: SubTask, reason: str) -> list[dict] | None:
        """Dynamically re-plan remaining steps when a subtask fails."""
        try:
            remaining = [
                {"id": st.id, "description": st.description, "tool_hint": st.tool_hint}
                for st in mission.subtasks if st.id != failed_subtask.id and st.status != "done"
            ]
            prompt = (
                f"An autonomous subtask failed.\n"
                f"Goal: {mission.goal}\n"
                f"Failed Step: {failed_subtask.description}\n"
                f"Failure Details: {reason[:300]}\n"
                f"Remaining Plan: {json.dumps(remaining)}\n"
                'Can we adapt the remaining steps? Reply ONLY valid JSON: '
                '{"replan": true, "updated_subtasks": [{"id": "r1", "description": "...", "tool_hint": "..."}]} '
                'or {"replan": false}'
            )
            raw = llm.chat([
                {"role": "system", "content": "You adapt autonomous execution plans when steps fail."},
                {"role": "user", "content": prompt}
            ], temperature=0.2, timeout=20, role="fast")
            data = json.loads(raw)
            if data.get("replan") and data.get("updated_subtasks"):
                return data.get("updated_subtasks")
        except Exception:
            pass
        return None

    def stop(self) -> None:
        """Emergency stop: abort all active autonomous missions."""
        with self._lock:
            for m in self.missions.values():
                if m.status == "running":
                    m.status = "aborted"
            self._save_state()

    def _should_ask_user(self, mission: Mission, subtask: SubTask, result: dict) -> dict | None:
        """Checkpoint: detect if an ambiguous situation or branching decision needs human input."""
        speech = str(result.get("speech", "")).lower()
        markers = ("choose", "select", "options", "which one", "multiple found", "several ways", "please confirm")
        if any(m in speech for m in markers):
            return {
                "question": f"Decision needed for mission #{mission.id}: {result.get('speech', '')[:180]}",
                "subtask": subtask.id,
            }
        return None

    def execute_goal(self, goal: str, background: bool = True, max_steps: int = 30, context: dict | None = None) -> dict:
        # If the goal has 3+ independent subtasks, delegate to swarm for parallel DAG execution
        mission = _decompose(goal, context)
        parallel_count = sum(1 for st in mission.subtasks if not getattr(st, "depends_on", None))
        if parallel_count >= 3:
            log.info("Mission %s has %d independent subtasks — delegating to SwarmOrchestrator", mission.id, parallel_count)
            try:
                from . import swarm as _swarm
                orc = _swarm.get_swarm_orchestrator()
                return orc.execute(goal, background=background)
            except Exception as exc:
                log.warning("Swarm delegation failed, falling back to sequential: %s", exc)

        with self._lock:
            running_count = sum(1 for m in self.missions.values() if m.status == "in_progress")
            if running_count >= 5:
                return {
                    "ok": False,
                    "speech": "Concurrent mission limit reached (5 active missions). Please wait for ongoing tasks to complete.",
                    "data": {"running_count": running_count},
                }
            self.missions[mission.id] = mission
            self._save_state()

        log.info("Mission %s started: %s (%d subtasks)", mission.id, goal, len(mission.subtasks))

        if background:
            t = threading.Thread(target=self._run_mission, args=(mission,), daemon=True, name=f"auto-{mission.id}")
            t.start()
            return {
                "ok": True,
                "speech": f"Autonomous mission #{mission.id} started: {goal}. I'll execute it step by step.",
                "data": {"mission_id": mission.id, "subtasks": len(mission.subtasks)},
            }
        else:
            self._run_mission(mission)
            return {
                "ok": mission.status == "done",
                "speech": f"Mission #{mission.id} {mission.status}: {mission.goal}",
                "data": {"mission_id": mission.id, "status": mission.status},
            }

    def _run_mission(self, mission: Mission) -> None:
        mission.status = "running"
        tracker = get_outcome_tracker()
        lib = get_strategy_lib()

        all_ok = True
        for st in list(mission.subtasks):
            if mission.status != "running":
                break
            st.status = "running"
            res = self._execute_subtask(mission, st)
            st.attempts += 1
            st.result = res.get("speech", "") or str(res.get("data", ""))
            tracker.record(mission.id, mission.goal, st.id, st.tool_hint, res.get("ok", False), st.result)

            # Verification loop: verify progress
            verification = self._verify_progress(mission, st, res)
            if not verification.get("closer", True):
                log.info("Mission %s: step %s diverged: %s", mission.id, st.id, verification.get("assessment"))

            # Human-in-the-loop checkpoint
            ask = self._should_ask_user(mission, st, res)
            if ask:
                mission.status = "waiting_confirm"
                mission.context["waiting_ask"] = ask
                self._save_state()
                bus.publish("confirm.required", {
                    "mission_id": mission.id,
                    "subtask_id": st.id,
                    "question": ask["question"],
                    "options": ["Proceed", "Abort", "Retry"],
                })
                bus.publish("notify.out", {
                    "kind": "autonomy.confirm",
                    "text": ask["question"],
                })
                log.info("Mission %s paused waiting for human confirmation", mission.id)
                break

            if res.get("ok", False):
                st.status = "done"
            else:
                # Try adaptive re-planning
                replan_subtasks = self._replan(mission, st, st.result)
                if replan_subtasks:
                    idx = mission.subtasks.index(st)
                    new_tasks = [
                        SubTask(
                            id=rst.get("id", f"rp{i+1}"),
                            description=rst.get("description", ""),
                            tool_hint=rst.get("tool_hint", "task_start"),
                        )
                        for i, rst in enumerate(replan_subtasks)
                    ]
                    mission.subtasks = mission.subtasks[:idx + 1] + new_tasks
                    log.info("Mission %s: adapted plan with %d new steps", mission.id, len(new_tasks))
                    st.status = "done"
                    st.result = "re-planned around failure"
                    continue

                alt = self._try_alternative(mission, st)
                if alt.get("ok", False):
                    st.status = "done"
                    st.result = "completed via alternative approach"
                else:
                    st.status = "failed"
                    all_ok = False
                    break

        mission.status = "done" if all_ok else "failed"
        mission.completed_at = time.time()
        lib.record_outcome(mission.goal, mission.strategy_used, all_ok, f"Status: {mission.status}")
        record_learned_strategy(mission.goal, mission.subtasks, all_ok, mission.completed_at - mission.created_at, mission.strategy_used)
        with self._lock:
            self._save_state()

        bus.publish("notify.out", {
            "kind": "autonomy",
            "text": f"Mission #{mission.id} {mission.status}: {mission.goal}",
        })
        log.info("Mission %s finished: %s", mission.id, mission.status)

    def _get_subtask(self, mission: Mission, st_id: str) -> SubTask | None:
        for st in mission.subtasks:
            if st.id == st_id:
                return st
        return None

    def _execute_subtask(self, mission: Mission, subtask: SubTask) -> dict:
        tool_name = subtask.tool_hint or "deep_research"
        if not is_allowed(tool_name):
            return {"ok": False, "speech": f"Tool {tool_name} not permitted in current tier", "data": {}}
        if tool_name.startswith("browser_"):
            res = self._execute_browser_task(mission, subtask)
        elif tool_name == "swarm_dispatch":
            try:
                from . import swarm as _swarm
                orc = _swarm.get_swarm_orchestrator()
                res = orc.execute(subtask.description, background=False)
            except Exception as exc:
                log.warning("Swarm subtask failed: %s", exc)
                res = {"ok": False, "speech": f"Swarm dispatch failed: {exc}", "data": {}}
        else:
            res = self._execute_generic_task(mission, subtask)

        # Automatic fallback: If tool failed, try next-best tool from Strategy/Outcome library
        if not res.get("ok"):
            log.info("Mission %s: tool '%s' failed, checking strategy library for alternatives...", mission.id, tool_name)
            alt_res = self._try_alternative(mission, subtask)
            if alt_res.get("ok"):
                return alt_res
        return res

    def _execute_generic_task(self, mission: Mission, subtask: SubTask, exec_prompt: str | None = None) -> dict:
        tool_name = subtask.tool_hint or "deep_research"
        args = self._build_tool_args(tool_name, subtask, mission)
        try:
            return tools.call(tool_name, args)
        except Exception as exc:
            return {"ok": False, "speech": f"Error: {exc}", "data": {}}

    def _execute_browser_task(self, mission: Mission, subtask: SubTask, exec_prompt: str = "") -> dict:
        prompt = (
            f'Given this task: "{subtask.description}"\n'
            'Determine next browser action. Respond ONLY JSON:\n'
            '{"action": "navigate|click|type|screenshot", "url": "...", "selector": "...", "text": "..."}\n'
            f'Current goal: {mission.goal}'
        )
        try:
            raw = llm.chat([
                {"role": "system", "content": "You control a web browser. Respond with JSON actions only."},
                {"role": "user", "content": prompt},
            ], temperature=0.2, timeout=20)
            action_data = json.loads(raw)
            action = action_data.get("action", "navigate")
            specific_tool = f"browser_{action}"
            if not is_allowed(specific_tool):
                return {"ok": False, "speech": f"Action {specific_tool} not permitted in current tier", "data": {}}

            b = get_browser()
            if action == "navigate":
                url = action_data.get("url", "https://google.com")
                from .tools.browser_tools import _nav_allowed
                if not _nav_allowed(url):
                    return {"ok": False, "speech": f"URL blocked by allowlist: {url}", "data": {}}
                return b.navigate(url)
            elif action == "click":
                return b.click(action_data.get("selector", "button"))
            elif action == "type":
                return b.type_text(action_data.get("selector", "input"), action_data.get("text", ""))
            return b.screenshot()
        except Exception as exc:
            log.warning("Browser task execution error: %s", exc)
            return {"ok": False, "speech": f"Browser error: {exc}", "data": {}}

    def _try_alternative(self, mission: Mission, subtask: SubTask) -> dict:
        tracker_alts = []
        try:
            tracker = get_outcome_tracker()
            best = tracker.get_best_approaches(mission.goal, limit=3)
            for b in best:
                t = b.get("tool")
                if t and t not in tracker_alts:
                    tracker_alts.append(t)
        except Exception:
            pass

        alternatives = tracker_alts + ["deep_research", "web_search", "fs_write", "vault_write", "task_start"]
        for alt in alternatives:
            if alt != subtask.tool_hint and is_allowed(alt):
                log.info("Mission %s: trying learned alternative tool '%s'", mission.id, alt)
                args = self._build_tool_args(alt, subtask, mission)
                try:
                    res = tools.call(alt, args)
                    if res.get("ok"):
                        return res
                except Exception:
                    pass
        return {"ok": False, "speech": "No working alternatives", "data": {}}

    def _build_tool_args(self, tool_name: str, subtask: SubTask, mission: Mission) -> dict:
        if tool_name in ("deep_research", "research"):
            return {"topic": subtask.description}
        if tool_name in ("web_search", "search"):
            return {"query": subtask.description}
        if tool_name == "docs_create":
            return {
                "title": f"Mission {mission.id}: {subtask.id}",
                "content": f"# {subtask.description}\n\nGoal: {mission.goal}\n\n## Progress\n\nAuto-generated by EVO autonomy engine.\n",
            }
        if tool_name == "vault_write":
            return {"topic": f"mission-{mission.id}-{subtask.id}", "content": f"{subtask.description}\n"}
        return {"query": subtask.description}

    def _extract_args(self, subtask: SubTask) -> dict:
        return {"query": subtask.description}

    def get_status(self) -> dict:
        active = [m for m in self.missions.values() if m.status == "running"]
        return {
            "active_count": len(active),
            "total": len(self.missions),
            "missions": [
                {
                    "id": m.id,
                    "goal": m.goal,
                    "status": m.status,
                    "progress": f"{sum(1 for st in m.subtasks if st.status == 'done')}/{len(m.subtasks)}",
                    "strategy": m.strategy_used,
                }
                for m in list(self.missions.values())[-10:]
            ],
        }

    def stop_mission(self, mission_id: str) -> bool:
        with self._lock:
            if mission_id in self.missions:
                self.missions[mission_id].status = "stopped"
                self._save_state()
                return True
        return False

    def confirm_mission(self, mission_id: str, choice: str = "Proceed") -> dict:
        """Resume a mission that was paused waiting for human confirmation."""
        with self._lock:
            mission = self.missions.get(mission_id)
            if not mission:
                return {"ok": False, "speech": f"Mission #{mission_id} not found."}
            if mission.status != "waiting_confirm":
                return {"ok": False, "speech": f"Mission #{mission_id} is in status '{mission.status}', not waiting for confirmation."}
            choice_clean = choice.strip().lower()
            if choice_clean == "abort":
                mission.status = "stopped"
                self._save_state()
                return {"ok": True, "speech": f"Mission #{mission_id} aborted by user."}
            mission.status = "running"
            mission.context["user_confirmation"] = choice
            self._save_state()
            t = threading.Thread(target=self._run_mission, args=(mission,), daemon=True, name=f"auto-{mission.id}")
            t.start()
            return {"ok": True, "speech": f"Mission #{mission_id} resumed with choice: '{choice}'."}

    def _check_initiative_signals(self) -> None:
        """Read proactive signals from initiative_engine without auto-spawning unprompted missions."""
        if os.environ.get("EVO_AUTO_INITIATIVE", "0") != "1":
            return
        from .consent import get_consent_manager
        if not get_consent_manager().has_consent("autonomy_execute"):
            return
        try:
            from . import initiative_engine
            candidates = initiative_engine.gather_candidates()
            for cand in candidates:
                if isinstance(cand, str) and len(cand) > 10:
                    goal_text = f"Proactive initiative: {cand}"
                    already = any(m.goal == goal_text and m.status in ("running", "planning")
                                  for m in self.missions.values())
                    if not already:
                        log.info("Generating autonomous goal from initiative signal: %s", cand)
                        self.execute_goal(goal_text, background=True)
                        break
        except Exception as exc:
            log.debug("Initiative signal check error: %s", exc)


_runner: AutonomousRunner | None = None


def get_runner() -> AutonomousRunner:
    global _runner
    if _runner is None:
        _runner = AutonomousRunner()
    return _runner


class ContinuousAutonomyLoop:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mk2-autonomy-loop")
        self._thread.start()
        log.info("Continuous autonomy loop started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        runner = get_runner()
        while not self._stop.is_set():
            try:
                self._tick(runner)
            except Exception as exc:
                log.warning("Autonomy loop tick error: %s", exc)
            self._stop.wait(300)

    def _tick(self, runner: AutonomousRunner) -> None:
        from .kill_switch import get_kill_switch
        if get_kill_switch().is_active():
            log.debug("Continuous autonomy tick skipped: kill switch is active.")
            return
        self._check_proactive_actions(runner)
        runner._check_initiative_signals()

    def _check_proactive_actions(self, runner: AutonomousRunner) -> None:
        goals = self._load_auto_goals()
        now = time.time()
        for g in goals:
            if g.get("active") and now - g.get("last_run", 0) > (g.get("interval_hours", 24) * 3600):
                log.info("Proactively starting goal: %s", g.get("goal"))
                runner.execute_goal(g.get("goal", ""))
                g["last_run"] = now
                self._save_auto_goals(goals)
                break

    def _load_auto_goals(self) -> list[dict]:
        path = AUTO_DATA / "auto_goals.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except Exception as exc:
                log.warning("Failed loading auto goals: %s", exc)
                return []
        return []

    def _save_auto_goals(self, goals: list[dict]) -> None:
        path = AUTO_DATA / "auto_goals.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(goals, indent=2), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            log.warning("Failed saving auto goals: %s", exc)


_autonomy_loop: ContinuousAutonomyLoop | None = None


def get_autonomy_loop() -> ContinuousAutonomyLoop:
    global _autonomy_loop
    if _autonomy_loop is None:
        _autonomy_loop = ContinuousAutonomyLoop()
    return _autonomy_loop


# ---------------------------------------------------------------------------
# Registered Tools
# ---------------------------------------------------------------------------

@tool("autonomy_execute", "Execute a high-level goal autonomously.",
      {"goal": {"type": "string"}}, permission="execute")
def autonomy_execute(goal: str, background: bool = True) -> dict:
    if not goal.strip():
        return {"ok": False, "speech": "What goal should I work towards?", "data": {}}
    return get_runner().execute_goal(goal.strip(), background=background)


@tool("autonomy_status", "Check status of autonomous missions.", {}, permission="read")
def autonomy_status() -> dict:
    status = get_runner().get_status()
    ms = status.get("missions", [])
    if not ms:
        return {"ok": True, "speech": "No autonomous missions running.", "data": status}
    summary = "; ".join(f"#{m['id']} [{m['status']}] {m['goal']} ({m['progress']})" for m in ms[:3])
    return {"ok": True, "speech": f"{status['active_count']} active mission(s): {summary}", "data": status}


@tool("autonomy_stop", "Stop an autonomous mission.",
      {"mission_id": {"type": "string"}}, permission="execute")
def autonomy_stop(mission_id: str) -> dict:
    ok = get_runner().stop_mission(mission_id)
    speech = f"Mission {mission_id} stopped." if ok else f"Mission {mission_id} not found."
    return {"ok": ok, "speech": speech, "data": {}}


@tool("autonomy_permission", "Query the current autonomy permission level (safe|standard|extended|full).",
      {"level": {"type": "string", "default": ""}}, permission="admin")
def autonomy_permission(level: str = "") -> dict:
    cur = get_permission_level()
    desc = _PERMISSION_TIERS.get(cur, {}).get("description", "")
    if not level or level.lower().strip() == cur:
        return {"ok": True, "speech": f"Permission level: {cur} ({desc})", "data": {"level": cur}}
    return {
        "ok": False,
        "speech": "Autonomy level cannot be modified via tool execution. Adjust EVO_AUTONOMY_LEVEL in host environment or settings.",
        "data": {"level": cur},
    }


@tool("autonomy_loop", "Start, stop, or check status of continuous background autonomy loop.",
      {"action": {"type": "string", "default": "status"}}, permission="execute")
def autonomy_loop(action: str = "start") -> dict:
    loop = get_autonomy_loop()
    if action == "start":
        loop.start()
        return {
            "ok": True,
            "speech": "Continuous autonomy loop started. EVO will work on goals in the background.",
            "data": {"running": True},
        }
    elif action == "stop":
        loop.stop()
        return {"ok": True, "speech": "Continuous autonomy loop stopped.", "data": {"running": False}}
    return {"ok": True, "speech": "Autonomy loop status checked.", "data": {}}


@tool("autonomy_learn", "Record a learned strategy outcome for future goals.",
      {"pattern": {"type": "string"}, "strategy": {"type": "string"}, "success": {"type": "boolean", "default": True}},
      permission="execute")
def autonomy_learn(pattern: str, strategy: str, success: bool = True) -> dict:
    get_strategy_lib().record_outcome(pattern, strategy, success, details="user-taught")
    verdict = "success" if success else "failure"
    return {
        "ok": True,
        "speech": f"Learned: for '{pattern}' use '{strategy}' -- recorded as {verdict}.",
        "data": {},
    }


@tool("autonomy_auto_goal", "Register a recurring autonomous goal.",
      {"goal": {"type": "string"}, "interval_hours": {"type": "integer", "default": 24}},
      permission="execute")
def autonomy_auto_goal(goal: str, interval_hours: int = 24) -> dict:
    if not goal.strip():
        return {"ok": False, "speech": "What goal should I work on?", "data": {}}
    loop = get_autonomy_loop()
    goals = loop._load_auto_goals()
    goals.append({
        "id": uuid.uuid4().hex[:8],
        "goal": goal.strip(),
        "interval_hours": interval_hours,
        "active": True,
        "created": time.time(),
        "last_run": 0,
    })
    loop._save_auto_goals(goals)
    return {
        "ok": True,
        "speech": f"Added autonomous goal: '{goal}'. I'll work on this every {interval_hours}h.",
        "data": {},
    }


@tool("autonomy_browser", "Control browser for automation tasks.",
      {"action": {"type": "string", "default": "navigate"}, "url": {"type": "string", "default": ""},
       "selector": {"type": "string", "default": ""}, "text": {"type": "string", "default": ""}},
      permission="execute")
def autonomy_browser(action: str = "navigate", url: str = "", selector: str = "", text: str = "") -> dict:
    b = get_browser()
    if action == "navigate":
        if not url:
            return {"ok": False, "speech": "Provide a URL.", "data": {}}
        return b.navigate(url)
    elif action == "click":
        if not selector:
            return {"ok": False, "speech": "Provide a selector.", "data": {}}
        return b.click(selector)
    elif action == "type":
        if not selector or not text:
            return {"ok": False, "speech": "Provide selector and text.", "data": {}}
        return b.type_text(selector, text)
    elif action == "screenshot":
        return b.screenshot()
    return {"ok": False, "speech": f"Unknown action: {action}", "data": {}}


@tool("autonomy_learned", "View best learned strategies for a goal pattern.",
      {"pattern": {"type": "string"}}, permission="read")
def autonomy_learned(pattern: str) -> dict:
    best = get_strategy_lib().get_strategy(pattern)
    if not best:
        return {"ok": True, "speech": f"No learned strategies for '{pattern}' yet.", "data": {}}
    rate = best.get("success_rate", 0)
    return {
        "ok": True,
        "speech": f"Best strategy for '{pattern}': {best['name']} ({rate:.0%} success rate).",
        "data": best,
    }
