"""Orchestrator: one agent loop, tools, streaming events, fast-path intents."""
import json
import logging
import os
import re
import threading
import time
import uuid

log = logging.getLogger("mk2.brain")


def _log_event(subsystem: str, event: str, **kwargs):
    log.info("[%s] %s %s", subsystem, event, " ".join(f"{k}={v}" for k, v in kwargs.items()))


from datetime import datetime
from typing import Callable

from . import db, llm, memory, tools
from .bus import bus

MAX_STEPS = 3


class TurnCancelled(Exception):
    pass


def _fast_path(text: str) -> str | None:
    """Sub-100ms deterministic answers. Also serves as offline fallback."""
    t = text.lower().strip(" .!?").replace("'", "").replace("?", "")
    t = re.sub(r"\s+", " ", t)
    # Emergency Kill Switch (Instant sub-10ms halt)
    if re.search(r"\b(stop everything|emergency stop|kill autonomy|halt autonomy|stop autonomous mode)\b", t):
        try:
            from .kill_switch import get_kill_switch
            get_kill_switch().stop_all("Voice emergency stop")
        except Exception:
            pass
        return "Emergency stop confirmed. Autonomous mode halted and consent reverted to assist-only."

    if t in ("time", "the time", "what time", "whats the time",
             "what is the time", "what time is it", "current time"):
        return datetime.now().strftime("%H:%M.")
    if t in ("date", "today", "todays date", "what is the date",
             "whats the date", "what day is it", "what is today"):
        return datetime.now().strftime("%A, %d %B.")
    if re.search(r"who are you|what are you|your name", t):
        return "EVO. What do you need?"
    if re.search(r"what can you do|help|capabilities", t):
        return "Whatever you need. Try me."
    # Math
    m = re.search(r"what is\s+([\d\s+\-*/^%.]+)", t)
    if m:
        expr = m.group(1).strip()
        try:
            result = eval(expr, {"__builtins__": {}}, {})
            return f"{expr} equals {result}."
        except Exception:
            pass

    # Direct website fast-path
    if t in ("youtube", "yt", "open youtube", "open yt", "launch youtube", "go to youtube"):
        import webbrowser
        webbrowser.open("https://www.youtube.com")
        return "Opening YouTube, sir."
    if t in ("github", "open github", "open gh"):
        import webbrowser
        webbrowser.open("https://github.com")
        return "Opening GitHub, sir."
    if t in ("gmail", "open gmail", "mail", "open mail"):
        import webbrowser
        webbrowser.open("https://mail.google.com")
        return "Opening Gmail, sir."
    if t in ("google", "open google"):
        import webbrowser
        webbrowser.open("https://www.google.com")
        return "Opening Google, sir."

    # App & Web launching
    app_map = {
        "notepad": ("notepad.exe", "Opening Notepad."),
        "calculator": ("calc.exe", "Opening Calculator."),
        "chrome": ("chrome.exe", "Opening Chrome."),
        "edge": ("msedge.exe", "Opening Edge."),
        "file explorer": ("explorer.exe", "Opening File Explorer."),
        "task manager": ("taskmgr.exe", "Opening Task Manager."),
        "settings": ("start ms-settings:", "Opening Settings."),
        "terminal": ("wt.exe", "Opening Terminal."),
        "cmd": ("cmd.exe", "Opening Command Prompt."),
        "youtube": ("https://www.youtube.com", "Opening YouTube, sir."),
        "github": ("https://github.com", "Opening GitHub, sir."),
        "gmail": ("https://mail.google.com", "Opening Gmail, sir."),
    }
    m = re.fullmatch(r"open\s+(?:the\s+)?(.+)", t)
    if m:
        app = m.group(1).strip()
        if app in app_map:
            try:
                target, speech = app_map[app]
                if target.startswith("http://") or target.startswith("https://"):
                    import webbrowser
                    webbrowser.open(target)
                else:
                    import subprocess
                    subprocess.Popen(target, shell=True)
                return speech
            except Exception:
                pass
        return f"Trying to open {app}."
    # Weather
    if re.search(r"weather", t):
        try:
            from . import tools
            m_city = re.search(r"in\s+([a-zA-Z\s]+)$", t)
            city = m_city.group(1).strip() if m_city else ""
            r = tools.call("weather_now", {"city": city})
            return r.get("speech") or "I couldn't get the weather right now."
        except Exception:
            return "Weather check is unavailable right now."
    # Timer
    m = re.search(r"(?:set|start)\s+(?:a\s+)?timer\s+(?:for\s+)?(.+)", t)
    if m:
        try:
            from . import tools
            r = tools.call("timer_set", {"duration": m.group(1).strip(), "label": "Timer"})
            return r.get("speech") or "Timer set."
        except Exception:
            return "I can't set timers right now."
    return None


def _is_online() -> bool:
    """Fast network connectivity probe."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://1.1.1.1", timeout=1.5):
            return True
    except Exception:
        return False


def _handle_offline(text: str) -> str | None:
    """Offline-capable responses when remote network is severed."""
    t = text.lower().strip()
    if any(k in t for k in ("weather", "latest news", "search the web", "search google", "check email")):
        return "I'm currently offline — web searches and remote mail are unavailable. I can still control your PC, execute local tools, and access memory."
    if any(k in t for k in ("calendar", "schedule", "my agenda", "upcoming events")):
        try:
            from . import vault
            notes = [n for n in vault.list_notes() if "calendar" in n.get("topic", "").lower()]
            if notes:
                return f"Cached calendar note: {notes[0].get('topic')}"
        except Exception:
            pass
    return None


def sanitize_final(text: str) -> str:
    """NEVER leak internal protocol, tool names, or internal monologue into the conversation."""
    out = (text or "").strip()

    # Remove any TOOL RESULT markers
    out = re.sub(r"^\s*TOOL RESULT.*$", "", out, flags=re.MULTILINE)

    # Strip XML tool calls, function tags, parameter tags
    out = re.sub(r"<tool_call>.*?</tool_call>", "", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<(?:function|tool)=.*?>.*?</(?:function|tool)>", "", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"<parameter=.*?>.*?</parameter>", "", out, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"</?(?:tool_call|function|tool|parameter)[^>]*>", "", out, flags=re.IGNORECASE)

    # Strip ⚙ tool lines and tool icons
    out = re.sub(r"^\s*⚙.*$", "", out, flags=re.MULTILINE)
    out = re.sub(r"⚙\s*[a-zA-Z0-9_\-]+(?:\s*\(.*?\))?", "", out)
    out = re.sub(r"\[(?:tool|calling tool):\s*[a-zA-Z0-9_\-]+.*?\]", "", out, flags=re.IGNORECASE)

    # Strip internal tool monologue & rationalizations out loud
    monologue_patterns = [
        r"that tool didn['’]?t pan out[.,! -]*",
        r"let me try a different angle[.,! -]*",
        r"let me try another (?:tool|approach|method|angle)[.,! -]*",
        r"i['’]?ll kick off a (?:research )?mission to[.,! -]*",
        r"looking at the tool results?[.,! -]*",
        r"the tool (?:failed|errored|returned)[.,! -]*",
        r"i tried running (?:the )?[a-z0-9_\-]+(?: tool)?[.,! -]*",
    ]
    for pat in monologue_patterns:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)

    # Strip third-party vendor claims and protect EVO identity
    out = re.sub(r"\bI am Claude(?:\s+(?:Haiku|Sonnet|Opus))?\b", "I am EVO", out, flags=re.IGNORECASE)
    out = re.sub(r"\bI['’]?m Claude(?:\s+(?:Haiku|Sonnet|Opus))?\b", "I'm EVO", out, flags=re.IGNORECASE)
    out = re.sub(r"\b(?:built|developed|created|made) by Anthropic\b", "built by you", out, flags=re.IGNORECASE)
    out = re.sub(r"\bI aim to be helpful, harmless, and honest[.,! -]*", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\bas an ai(?: language model)?[.,! -]*", "", out, flags=re.IGNORECASE)

    # Remove any JSON objects that look like tool calls or say-wrappers
    json_blocks = re.findall(r"\{[^{}]*\}", out)
    for jb in json_blocks:
        parsed = None
        try:
            parsed = json.loads(jb)
        except Exception:
            pass
        if isinstance(parsed, dict) and ("tool" in parsed or "say" in parsed or "action" in parsed):
            replacement = str(parsed.get("say", "")).strip()
            out = out.replace(jb, replacement)
        elif isinstance(parsed, dict) and "name" in parsed and "args" in parsed:
            # vault_read / vault_search style tool echoes
            out = out.replace(jb, "")

    # Remove any remaining bare JSON that looks like a tool invocation
    out = re.sub(
        r'\{\s*"(?:name|tool)"\s*:.*?\}',
        "", out, flags=re.DOTALL
    )

    # Clean up whitespace
    out = re.sub(r"\n{3,}", "\n\n", out)
    res = out.strip()[:2000]

    # Phase 5: Enforce Persona Validation
    try:
        from . import response_validator
        _, validated = response_validator.validate_response(res)
        return validated
    except Exception:
        return res

def json_loads(s: str):
    import json

    return json.loads(s)


def parse_tool_call(raw: str) -> dict | None:
    if not raw or not isinstance(raw, str):
        return None
    raw_s = raw.strip()

    # 1. Try standard JSON extraction
    text = re.sub(r"^```(?:json)?|```$", "", raw_s, flags=re.MULTILINE).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    if "tool" in obj and "args" in obj:
                        return obj
                    if "name" in obj and "arguments" in obj:
                        return {"tool": obj["name"], "args": obj["arguments"]}
                    if "function" in obj and "parameters" in obj:
                        return {"tool": obj["function"], "args": obj["parameters"]}
                    if "tool" in obj and isinstance(obj["tool"], str):
                        args = {k: v for k, v in obj.items() if k not in ("tool", "say")}
                        return {"tool": obj["tool"], "args": obj.get("args", args)}
                    if "action" in obj and isinstance(obj["action"], str) and obj["action"] != "say":
                        return {"tool": obj["action"], "args": obj.get("args", {})}
                    if "say" in obj:
                        return obj
            except ValueError:
                continue

    # 2. XML / Tag format: <tool_call> ... </tool_call> or <function=name> ... </function>
    m_func = re.search(r"<(?:function|tool)=([a-zA-Z0-9_\-]+)>(.*?)(?:</(?:function|tool)>|$)", raw_s, re.DOTALL)
    if m_func:
        name = m_func.group(1).strip()
        body = m_func.group(2).strip()
        params = {}
        param_matches = re.findall(r"<parameter=([a-zA-Z0-9_\-]+)>(.*?)</parameter>", body, re.DOTALL)
        if param_matches:
            for p_k, p_v in param_matches:
                params[p_k.strip()] = p_v.strip()
        elif body:
            try:
                p_obj = json.loads(body)
                if isinstance(p_obj, dict):
                    params = p_obj
                else:
                    params = {"query": str(p_obj)}
            except Exception:
                if name in ("web_search", "google_search", "browser_search"):
                    params = {"query": body}
                elif name in ("tool_help", "open_app", "close_app"):
                    params = {"name": body} if name == "tool_help" else {"target": body}
                else:
                    params = {"input": body}
        return {"tool": name, "args": params}

    # 3. <tool_call> containing JSON or function name
    m_tc = re.search(r"<tool_call>(.*?)</tool_call>", raw_s, re.DOTALL)
    if m_tc:
        tc_body = m_tc.group(1).strip()
        for i, ch in enumerate(tc_body):
            if ch == "{":
                try:
                    obj, _ = decoder.raw_decode(tc_body[i:])
                    if isinstance(obj, dict):
                        if "name" in obj:
                            return {"tool": obj["name"], "args": obj.get("arguments", obj.get("args", {}))}
                        if "tool" in obj:
                            return {"tool": obj["tool"], "args": obj.get("args", {})}
                except ValueError:
                    continue
        m_name = re.match(r"^([a-zA-Z0-9_\-]+)(?::\s*(.*))?$", tc_body)
        if m_name:
            fn = m_name.group(1)
            arg = m_name.group(2) or ""
            return {"tool": fn, "args": {"query": arg} if arg else {}}

    return None


def _timed_tool_call(name: str, args: dict, timeout_sec: float = 10.0) -> dict:
    """Execute tools with a hard timeout to prevent hangs or 2-minute freezes."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tools.call, name, args)
        try:
            return future.result(timeout=timeout_sec)
        except TimeoutError:
            return {"ok": False, "speech": f"Tool '{name}' timed out after {int(timeout_sec)}s."}
        except Exception as exc:
            return {"ok": False, "speech": f"Tool '{name}' error: {str(exc)[:80]}"}


CORE_TOOLS = {"web_search", "deep_thought", "docs_create",
              "youtube_summarize", "remember_episode", "task_start"}


def _is_tool_intent(text: str) -> bool:
    """Check if the user prompt requires external actions, PC operations, search, or storage."""
    t = (text or "").lower().strip()
    action_words = (
        "search", "google", "look up", "browse", "find", "weather",
        "open", "close", "launch", "kill", "restart", "shutdown",
        "create file", "write note", "save", "vault", "reminder", "timer",
        "wifi", "bluetooth", "volume", "brightness", "screenshot", "screen",
        "email", "mail", "calendar", "event", "schedule", "run command",
        "execute", "terminal", "powershell", "cmd", "summarize video", "youtube",
        "calculate", "check status", "system info", "device",
        "automate", "play song", "play music", "play on youtube"
    )
    return any(w in t for w in action_words)


def compact_manifest(text: str, manifest: list[dict]) -> str:
    """Full specs only for tools relevant to this message; the rest ship as
    bare names (prompt diet — prefill time scales with prompt size)."""
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    scored = []
    for t in manifest:
        hay = (t["name"] + " " + t["description"]).lower()
        overlap = len(words & set(re.findall(r"[a-z]{3,}", hay)))
        scored.append((overlap, t["name"], t))
    scored.sort(key=lambda x: (-x[0], x[2]["name"]))
    detailed_names = {name for s, name, _t in scored[:12] if s > 0}
    detailed_names |= CORE_TOOLS

    def _fmt(t: dict) -> str:
        return f"- {t['name']}: {t['description'][:90]}"

    detailed_lines = [_fmt(t) for t in manifest if t["name"] in detailed_names]
    other_names = sorted(t["name"] for t in manifest
                         if t["name"] not in detailed_names)
    manifest_text = "\n".join(detailed_lines)
    if other_names:
        manifest_text += ("\nOther tools (use tool_help <name> for usage): "
                          + ", ".join(other_names))
    return manifest_text


def _is_complex_goal(text: str) -> bool:
    """Detect if a user prompt explicitly requests a background autonomous mission."""
    t = (text or "").strip().lower()
    keywords = (
        "start mission", "autonomous mission", "run in background",
        "in the background", "background task", "background mission"
    )
    return any(k in t for k in keywords)


def _is_money_analysis_question(text: str) -> bool:
    """Return True if this is an analysis/recommendation question, not an action."""
    analysis_indicators = (
        "how am i doing", "how's my business", "what should i", "show me my",
        "am i on track", "how much", "what's my", "my numbers", "my stats",
        "overview", "briefing", "summary", "how's it going", "how is it going",
        "what do you think", "recommend", "advice", "insight", "analysis",
        "doing well", "doing bad", "improve", "better at",
    )
    action_indicators = (
        "send", "create", "submit", "record", "add", "delete", "update",
        "invoice", "proposal", "payment", "follow up", "contact",
    )
    lower = text.lower()
    if any(a in lower for a in action_indicators):
        return False
    if any(a in lower for a in analysis_indicators):
        return True
    money_keywords = ("money", "earn", "pipeline", "client", "proposal", "invoice", "revenue", "income", "leads", "upwork", "freelance", "business", "pricing", "rate")
    return any(k in lower for k in money_keywords) and "?" in text


def handle_turn(
    text: str,
    surface: str = "console",
    on_event: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    voice: bool = False,
) -> str:
    """Full turn pipeline. Emits events: thinking|tool|delta|done|error.

    voice=True routes generation down the fast ladder (role="fast") so
    spoken replies skip the slow high-intelligence models; typed chat keeps
    role="primary"."""
    turn_id = uuid.uuid4().hex[:12]

    def emit(ev: dict) -> None:
        if on_event:
            on_event(ev)

    def check_cancel() -> bool:
        return bool(cancelled and cancelled())

    if not (text or "").strip():
        reply = "I didn't catch that."
        emit({"type": "done", "text": reply})
        return reply

    t0 = time.time()
    db.trace(turn_id, "start", 0)

    # Direct Money Analysis path via MoneyIntelligence.answer()
    money_keywords = ("money", "earn", "pipeline", "client", "proposal", "invoice", "revenue", "income", "leads", "upwork", "freelance", "business", "pricing", "rate")
    money_hit = any(k in text.lower() for k in money_keywords)
    if money_hit and _is_money_analysis_question(text):
        try:
            from .money_intelligence import get_money_intelligence
            reply = get_money_intelligence().answer(text)
            if reply:
                reply = reply.strip()
                memory.record_turn(text, reply, surface)
                emit({"type": "done", "text": reply})
                if surface != "console":
                    bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": reply})
                return reply
        except Exception as exc:
            log.warning("MoneyIntelligence answer fallback: %s", exc)

    try:
        from . import conversation
        flow_res = conversation.evaluate_turn_intent(text)
        if flow_res.get("immediate_reply"):
            immediate = flow_res["immediate_reply"]
            memory.record_turn(text, immediate, surface)
            emit({"type": "done", "text": immediate})
            if surface != "console":
                bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": immediate})
            return immediate
        if flow_res.get("transformed_text"):
            text = flow_res["transformed_text"]
    except Exception:
        pass

    # Fast-lane: obvious commands execute instantly, zero model calls.
    from .fastlane import fast_command

    instant = fast_command(text, surface=surface)
    if instant is not None:
        memory.record_turn(text, instant, surface)
        emit({"type": "done", "text": instant})
        db.trace(turn_id, "total_fastcmd", (time.time() - t0) * 1000)
        if surface != "console":  # console renders locally
            bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": instant})
        return instant

    try:
        from . import conversation
        corr = conversation.detect_correction(text)
        if corr and corr != text:
            db.remember_fact(f"correction:{int(time.time())}", f"User clarified: {corr}", source="correction")
    except Exception:
        pass

    if not _is_online():
        off = _handle_offline(text)
        if off:
            memory.record_turn(text, off, surface)
            emit({"type": "done", "text": off})
            if surface != "console":
                bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": off})
            return off

    fast = _fast_path(text)
    if fast:
        memory.record_turn(text, fast, surface)
        emit({"type": "done", "text": fast})
        db.trace(turn_id, "total_fastpath", (time.time() - t0) * 1000)
        if surface != "console":  # console renders locally
            bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": fast})
        return fast

    # Phase 1: Deliberation and structured planning gate
    from . import planner
    if (surface not in ("voice", "web") or os.environ.get("EVO_PLANNER_ENABLED") == "1") and planner.should_plan(text, surface=surface):
        try:
            log.info("Goal qualifies for deliberate planning: '%s'", text[:60])
            plan_obj = planner.plan(text, context=f"Surface: {surface}")
            if plan_obj and len(plan_obj.steps) >= 2:
                emit({"type": "plan", "steps": len(plan_obj.steps), "rationale": plan_obj.rationale})
                res = planner.execute_plan(plan_obj, emit=emit, check_cancel=check_cancel)
                reply = res.get("answer") or f"Executed plan for: {text}"
                memory.record_turn(text, reply, surface)
                emit({"type": "done", "text": reply})
                if surface != "console":
                    bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": reply})
                return reply
        except Exception as exc:
            log.warning("Deliberate planning fallback to main turn loop: %s", exc)

    # Phase 8: Complex goal routing directly to autonomy engine
    if _is_complex_goal(text) and os.environ.get("EVO_AUTONOMY_DISPATCH", "1") == "1":
        try:
            from . import autonomy

            res = autonomy.get_runner().execute_goal(text, background=True)
            reply = res.get("speech") or f"Starting autonomous mission to {text}. Working on it now."
            memory.record_turn(text, reply, surface)
            emit({"type": "done", "text": reply})
            if surface != "console":
                bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": reply})
            return reply
        except Exception as exc:
            log.warning("Autonomy goal delegation failed: %s", exc)

    messages = memory.build_context_messages(text, surface)
    manifest = tools.manifest()
    manifest_text = compact_manifest(text, manifest)
    system_extra = (
        f"\nTOOLS (exact names only):\n{manifest_text}\n"
        'To act, reply ONLY {"tool":"name","args":{...}}. After tool results settle, '
        'reply ONLY {"say":"<final spoken answer>"} interpreting results naturally - '
        "never dump raw output, never mention internal steps or tool names to the user.\n"
        "SPEECH RULE: write like a person talking. Be concise, direct, and structured. "
        "CRITICAL: Never mention internal tools, tool failures, or say 'That tool didn't pan out' or 'Let me try a different angle'. Deliver only your final helpful answer directly to the user.\n"
        "EXECUTION RULE: if asked for a report, file, document, note or deliverable - "
        "DO IT: research what you need, then CREATE the real artifact with "
        "docs_create/fs_write/vault_write and tell them where it is.\n"
        "HONESTY & CAPABILITY RULES:\n"
        "- Be 100% honest about what you can and cannot do on this PC.\n"
        "- You CAN: open apps/sites, search the live web with web_search, control Windows settings (volume, brightness, wifi, lock, sleep), set timers/reminders, read the screen, create real documents/notes, manage CRM leads, draft proposals & invoices, and scan freelance opportunities.\n"
        "- You CANNOT: execute unauthorized financial transfers or send unapproved spam outreach without user confirmation.\n"
        "- When asked about making money or tracking revenue, actively reference live CRM data, client pipeline milestones, and suggest high-ROI freelance opportunities, proposals, or content assets.\n"
        "ACTION & PROMISES RULE:\n"
        "- NEVER say 'I'm digging into data', 'Give me a moment to check', or 'Let me research that' without calling a tool in that same step!\n"
        "- If research is needed, you MUST call {\"tool\": \"deep_research\", \"args\": {\"topic\": \"...\"}} or {\"tool\": \"web_search\", \"args\": {\"query\": \"...\"}}.\n"
        "- When deep_research is called, it automatically runs in the background and saves a real briefing to your vault."
    )

    # Money & Business Intelligence Context Injection
    money_keywords = ("money", "earn", "pipeline", "client", "proposal", "invoice", "revenue", "income", "leads", "upwork", "freelance", "business", "pricing", "rate")
    if any(k in text.lower() for k in money_keywords):
        try:
            from .money_intelligence import get_money_intelligence
            mi = get_money_intelligence()
            money_ctx = mi.get_context()
            system_extra += (
                f"\n=== YOUR BUSINESS & FINANCIAL CONTEXT ===\n"
                f"{money_ctx}\n"
                f"=== END OF BUSINESS CONTEXT ===\n"
                "When the user asks about money, revenue, pipeline, or clients, use this real data to reason directly. "
                "You understand their business — answer conversationally with specific numbers and actionable recommendations. "
                "Do NOT call tools for analysis questions — you already have the full data snapshot. Use tools only for actions (submitting proposals, sending invoices, recording payments).\n"
            )
        except Exception:
            pass

    # Universal Relevant Knowledge & Distilled Actionable Skills Injection
    try:
        from .knowledge import get_knowledge_synthesizer
        from .skills import get_skill_extractor
        related_k = get_knowledge_synthesizer().get_related(text)
        related_s = get_skill_extractor().get_relevant_skills(text)

        k_blocks = []
        if related_k:
            k_lines = "\n".join(f"[{i+1}] {r.get('title', '')}: {r.get('snippet', '')[:200]}" for i, r in enumerate(related_k[:3]))
            k_blocks.append(f"=== RELEVANT PRIOR KNOWLEDGE ===\n{k_lines}")

        if related_s:
            s_lines = "\n".join(f"- {s.get('procedure', '')}" for s in related_s[:3])
            k_blocks.append(f"=== RELEVANT ACTIONABLE PROCEDURES ===\n{s_lines}\nApply these procedures directly when relevant.")

        if k_blocks:
            system_extra += "\n\n" + "\n\n".join(k_blocks) + "\n"
    except Exception:
        pass

    messages[0]["content"] += system_extra

    answer = ""
    fail_streak = 0
    failed_tool_sigs: set[str] = set()
    last_fail_speech = ""
    max_steps = 2 if (voice or surface == "voice") else MAX_STEPS
    TURN_BUDGET = 15.0 if (voice or surface == "voice") else 25.0

    for step in range(max_steps):
        if check_cancel():
            raise TurnCancelled()
        remaining = TURN_BUDGET - (time.time() - t0)
        if remaining <= 0:
            break
        # Last-step nudge: force a final spoken answer instead of more tools
        if step == max_steps - 1 and step > 0:
            messages.append({"role": "system",
                             "content": ("FINAL STEP: you must finish NOW. "
                                         'Reply ONLY {"say": "..."} with your '
                                         "best answer to the user. Do not "
                                         "call any more tools.")})
        emit({"type": "thinking"})
        parts: list[str] = []
        emit_mode: bool | None = None
        role = "voice" if (voice or surface == "voice") else "primary"
        step_timeout = min(10 if role == "voice" else 20, max(4, int(remaining)))
        try:
            for delta in llm.chat_stream(messages, temperature=0.4,
                                         role=role, timeout=step_timeout):
                if check_cancel():
                    raise TurnCancelled()
                parts.append(delta)
                if emit_mode is None:
                    first = "".join(parts).lstrip()
                    if not first:
                        continue
                    emit_mode = not (first.startswith(("{", "```", "<", "`")) or "tool_call" in first or "function=" in first)
                if emit_mode:
                    emit({"type": "delta", "text": delta})
        except llm.LLMStreamStalled as stalled:
            # winning route died mid-generation: reset UI, show a heartbeat,
            # and keep trying fresh route pairs; final fallback = one-shot
            # completion (cannot die mid-sentence).
            emit({"type": "reset"})
            emit({"type": "progress", "text": "switching routes..."})
            parts = []
            emit_mode = None
            answer = ""
            recovered = False
            for attempt in range(2):
                remaining = TURN_BUDGET - (time.time() - t0)
                if remaining <= 3:
                    break
                parts = []
                emit_mode = None
                try:
                    for delta in llm.chat_stream(messages, temperature=0.4,
                                                 role=role,
                                                 timeout=max(10, int(remaining))):
                        if check_cancel():
                            raise TurnCancelled()
                        parts.append(delta)
                        if emit_mode is None:
                            first = "".join(parts).lstrip()
                            if not first:
                                continue
                            emit_mode = not first.startswith(("{", "```"))
                        if emit_mode:
                            emit({"type": "delta", "text": delta})
                except llm.LLMStreamStalled:
                    emit({"type": "reset"})
                    emit({"type": "progress",
                          "text": "still switching routes..."})
                    continue
                except llm.LLMUnavailable:
                    continue
                raw = "".join(parts).strip()
                call = parse_tool_call(raw)
                if call and "say" in call:
                    answer = sanitize_final(str(call.get("say", "")))
                elif call and "tool" in call:
                    r2 = tools.call(str(call["tool"]), call.get("args") or {})
                    answer = sanitize_final(r2.get("speech", ""))
                else:
                    answer = sanitize_final(raw)
                recovered = True
                break
            if not recovered:
                # last resort: one-shot completion - cannot die mid-stream
                remaining = TURN_BUDGET - (time.time() - t0)
                try:
                    answer = sanitize_final(
                        llm.chat(messages, temperature=0.4,
                                 timeout=max(8, min(45, int(remaining)))))
                except Exception as exc2:
                    reply = (f"My language core dropped mid-reply "
                             f"({str(exc2)[:80]}). Try again?")
                    emit({"type": "error", "text": reply})
                    emit({"type": "done", "text": reply})
                    return reply
            break
        except llm.LLMUnavailable as exc:
            reply = f"My language core is unreachable right now ({str(exc)[:100]})."
            emit({"type": "error", "text": reply})
            emit({"type": "done", "text": reply})
            return reply
        raw = "".join(parts).strip()
        if not raw:
            # upstream returned an EMPTY response (dead route): tell the
            # model to actually answer and try again — never break silently
            messages.append({"role": "system",
                             "content": ("Your previous response was EMPTY. "
                                         "Reply now: either a tool call or "
                                         '{"say": "..."} — never nothing.')})
            continue
        call = parse_tool_call(raw)
        if call and "tool" in call:
            name = str(call.get("tool", "")).strip()
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"

            # Clear any partial reasoning text leaked to UI during this step
            emit({"type": "reset"})
            emit({"type": "tool", "name": name,
                  "brief": ", ".join(f"{k}={str(v)[:36]}" for k, v in list(args.items())[:2])})

            # Check if this exact tool call already failed in this turn (Deduplication)
            if call_sig in failed_tool_sigs:
                messages.append({
                    "role": "user",
                    "content": (
                        f"TOOL RESULT ({name}): FAILED - You already attempted this call with these arguments and it failed. "
                        "Do NOT retry it. Reply now with a direct, conversational answer using the information you have."
                    ),
                })
                fail_streak += 1
                if fail_streak >= 2:
                    answer = f"I could not complete that - {name.replace('_', ' ')} kept failing. Let me know if you would like to try another way."
                    break
                continue

            # Long-running tools become background jobs: reply instantly,
            # stream progress, announce the result when done.
            manifest_by_name = {t["name"]: t for t in manifest}
            if manifest_by_name.get(name, {}).get("long_running"):
                tools.set_emitter(emit)

                def _bg_run(args=args):
                    result_bg = _timed_tool_call(name, args, timeout_sec=15.0)
                    tools.set_emitter(None)
                    speech_bg = str(result_bg.get("speech", ""))[:300]
                    bus.publish("notify.out", {"kind": name,
                                               "text": f"{name} finished: {speech_bg}"})

                threading.Thread(target=_bg_run, daemon=True,
                                 name=f"mk2-bg-{name}").start()
                ack = (f"Started {name.replace('_', ' ')} on '{args.get('topic', args.get('goal', ''))[:60]}'. "
                       "I'll report back here when it's done.")
                emit({"type": "done", "text": ack})
                memory.record_turn(text, ack, surface)
                if surface != "console":  # console renders locally
                    bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": ack})
                return ack

            tools.set_emitter(emit)
            result = _timed_tool_call(name, args, timeout_sec=10.0)
            tools.set_emitter(None)
            messages.append({"role": "assistant", "content": raw})
            if result.get("ok") is False:
                fail_streak += 1
                failed_tool_sigs.add(call_sig)
                fail_speech = str(result.get("speech", ""))[:150]
                messages.append({
                    "role": "user",
                    "content": (
                        f"TOOL RESULT ({name}): FAILED - {fail_speech}\n"
                        f"This failed {fail_streak}x. Do NOT retry the same call. Use a "
                        "different tool or different arguments, or finish honestly with "
                        '{"say": "..."} explaining what you could not do.'
                    ),
                })
                if fail_streak >= 2:
                    answer = (f"I looked into that, but encountered an issue with {name.replace('_', ' ')} "
                              f"({fail_speech}). Let me know how else I can assist.")
                    break
                continue
            fail_streak = 0
            messages.append({"role": "user",
                             "content": f"TOOL RESULT ({name}):\n{str(result)[:1800]}"})
            messages.append({"role": "system",
                             "content": ('Reply ONLY {"say": "..."} with your '
                                         "final answer to the user now.")})
            continue
        # final answer
        if "say" in (call or {}):
            answer = sanitize_final(str((call or {}).get("say", "")))
        elif call and "tool" in call:
            r_final = tools.call(str(call["tool"]), call.get("args") or {})
            answer = sanitize_final(r_final.get("speech", ""))
        else:
            ACTION_PROMISE_RE = re.compile(
                r"(?:i['’]?m (?:digging|looking|checking|scanning|searching|gathering|pulling).*?(?:now|moment|options|data|into)|"
                r"give me a (?:moment|second|minute) to (?:pull|gather|check|research|find|see))",
                re.IGNORECASE
            )
            if step == 0 and ACTION_PROMISE_RE.search(raw):
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "ACTION REQUIRED: You said you are digging into data or researching. "
                        "You must execute the action right now. Reply ONLY "
                        '{"tool": "deep_research", "args": {"topic": "<specific topic to research>"}} '
                        'or {"tool": "web_search", "args": {"query": "<search query>"}}.'
                    )
                })
                continue
            answer = sanitize_final(raw)

        # Sanitize final response and validate
        if answer:
            try:
                from .response_validator import validate_response
                _, validated = validate_response(answer)
                if validated:
                    answer = validated
            except Exception as exc:
                log.warning("Response validation failed: %s", exc)
        if answer:
            break

    if not answer:
        # last-chance rescue: one non-streaming attempt on the best route
        try:
            messages.append({"role": "system",
                             "content": "Provide a direct, complete, natural final answer to the user based on the conversation context. Do not use tool calls or JSON."})
            answer = sanitize_final(
                llm.chat(messages, temperature=0.4, timeout=20))
        except Exception:
            answer = ("I've processed your request. Let me know if you need any more details.")

    answer_prefix = ""
    uncertainty_patterns = ("i don't know", "i'm not sure", "i am not sure", "i have no information", "not familiar with")
    if any(p in answer.lower() for p in uncertainty_patterns) and len(text) > 10:
        answer_prefix = "I didn't have a great answer for that — I'm researching it now. "

    if answer_prefix and not answer.startswith(answer_prefix):
        answer = f"{answer_prefix}{answer}"

    emit({"type": "done", "text": answer})
    db.trace(turn_id, "total_agent", (time.time() - t0) * 1000, f"steps={step + 1}")
    if surface != "console":  # console renders locally
        bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": answer})

    # Background async post-turn housekeeping (zero latency overhead on user response)
    def _async_post_turn():
        try:
            memory.record_turn(text, answer, surface)
            memory.maybe_summarize(text, surface)
        except Exception:
            pass
        try:
            from . import conversation
            conversation.record_turn_completion(text, answer)
        except Exception:
            pass
        # Autonomous learning trigger on uncertainty (runs in daemon thread)
        uncertainty_patterns = ("i don't know", "i'm not sure", "i am not sure", "i have no information", "not familiar with")
        if any(p in answer.lower() for p in uncertainty_patterns) and len(text) > 10:
            try:
                import threading
                threading.Thread(
                    target=lambda: __import__("mk2.research_tools", fromlist=["deep_research"]).deep_research(text),
                    daemon=True,
                    name="mk2-auto-research"
                ).start()
            except Exception:
                pass
        try:
            context_snapshot = {
                "turn_id": turn_id,
                "text": text,
                "reply": answer,
                "surface": surface,
                "timestamp": time.time(),
                "recent_turns": [
                    {"role": r["role"], "content": (r["content"] or "")[:200], "ts": r.get("ts", 0)}
                    for r in db.recent_messages(6)
                ],
                "facts_loaded": [f["key"] for f in db.all_facts(10)],
            }
            bus.publish("convo.sync", context_snapshot)
        except Exception:
            pass

    threading.Thread(target=_async_post_turn, daemon=True, name=f"mk2-postturn-{turn_id}").start()
    return answer






