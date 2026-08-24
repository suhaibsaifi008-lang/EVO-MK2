"""Orchestrator: one agent loop, tools, streaming events, fast-path intents."""
import json
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Callable

from . import db, llm, memory, tools
from .bus import bus

MAX_STEPS = 10


class TurnCancelled(Exception):
    pass


def _fast_path(text: str) -> str | None:
    """Sub-100ms deterministic answers so simple asks never touch a model."""
    t = text.lower().strip(" .!?").replace("'", "").replace("?", "")
    t = re.sub(r"\s+", " ", t)
    if t in ("time", "the time", "what time", "whats the time",
             "what is the time", "what time is it", "current time"):
        return datetime.now().strftime("It is %H:%M.")
    if t in ("date", "today", "todays date", "what is the date",
             "whats the date", "what day is it", "what is today"):
        return datetime.now().strftime("Today is %A, %d %B %Y.")
    return None


def sanitize_final(text: str) -> str:
    """NEVER leak internal protocol into the conversation."""
    out = (text or "").strip()

    # Remove any TOOL RESULT markers
    out = re.sub(r"^\s*TOOL RESULT.*$", "", out, flags=re.MULTILINE)

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
    return out.strip()[:2000]

def json_loads(s: str):
    import json

    return json.loads(s)


def parse_tool_call(raw: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                if isinstance(obj, dict):
                    return obj
            except ValueError:
                continue
    return None


CORE_TOOLS = {"web_search", "deep_thought", "docs_create",
              "youtube_summarize", "remember_episode", "task_start"}


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


def handle_turn(
    text: str,
    surface: str = "console",
    on_event: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Full turn pipeline. Emits events: thinking|tool|delta|done|error."""
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

    # Fast-lane: obvious commands execute instantly, zero model calls.
    from .fastlane import fast_command

    instant = fast_command(text, surface=surface)
    if instant is not None:
        memory.record_turn(text, instant, surface)
        emit({"type": "done", "text": instant})
        db.trace(turn_id, "total_fastcmd", (time.time() - t0) * 1000)
        bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": instant})
        return instant

    fast = _fast_path(text)
    if fast:
        memory.record_turn(text, fast, surface)
        emit({"type": "done", "text": fast})
        db.trace(turn_id, "total_fastpath", (time.time() - t0) * 1000)
        bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": fast})
        return fast

    messages = memory.build_context_messages(text, surface)
    manifest = tools.manifest()
    manifest_text = compact_manifest(text, manifest)

    system_extra = (
        f"\nTOOLS (exact names only):\n{manifest_text}\n"
        'To act, reply ONLY {"tool":"name","args":{...}}. After tool results settle, '
        'reply ONLY {"say":"<final spoken answer>"} interpreting results naturally - '
        "never dump raw output, never mention internal steps or tool names to the user. "
        "When you have web_search results, ANSWER the question in your own words from "
        "them - do not read out page titles. For genuinely hard multi-angle questions "
        "(comparisons, decisions, tricky reasoning) prefer the deep_thought tool.\n"
        "SPEECH RULE: write like a person talking. If you announce options, points, "
        "steps or reasons you MUST list them immediately and completely - announcing "
        "without listing is forbidden. No corporate filler ('By implementing these "
        "strategies...'), no meta-commentary about your answer.\n"
        "EXECUTION RULE: if asked for a report, file, document, note or deliverable - "
        "DO IT: research what you need, then CREATE the real artifact with "
        "docs_create/fs_write/vault_write and tell them where it is. NEVER reply with "
        "just an outline, a plan, or steps you 'would' take. For huge asks (e.g. 1000 "
        "pages) build a complete sensible version instead and say exactly what you made."
    )
    messages[0]["content"] += system_extra

    answer = ""
    fail_streak = 0
    last_fail_speech = ""
    TURN_BUDGET = 45.0          # hard wall-clock cap for the whole turn

    for step in range(MAX_STEPS):
        if check_cancel():
            raise TurnCancelled()
        remaining = TURN_BUDGET - (time.time() - t0)
        if remaining <= 0:
            break
        # Last-step nudge: force a final spoken answer instead of more tools
        if step == MAX_STEPS - 1:
            messages.append({"role": "system",
                             "content": ("FINAL STEP: you must finish NOW. "
                                         'Reply ONLY {"say": "..."} with your '
                                         "best answer to the user. Do not "
                                         "call any more tools.")})
        emit({"type": "thinking"})
        parts: list[str] = []
        emit_mode: bool | None = None
        try:
            for delta in llm.chat_stream(messages, temperature=0.4):
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
            emit({"type": "tool", "name": name,
                  "brief": ", ".join(f"{k}={str(v)[:36]}" for k, v in list(args.items())[:2])})
            # Long-running tools become background jobs: reply instantly,
            # stream progress, announce the result when done.
            manifest_by_name = {t["name"]: t for t in manifest}
            if manifest_by_name.get(name, {}).get("long_running"):
                tools.set_emitter(emit)

                def _bg_run(args=args):
                    result_bg = tools.call(name, args)
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
                bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": ack})
                return ack

            tools.set_emitter(emit)
            result = tools.call(name, args)
            tools.set_emitter(None)
            messages.append({"role": "assistant", "content": raw})
            if result.get("ok") is False:
                fail_streak += 1
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
                if fail_streak >= 3:
                    answer = (f"I couldn't complete that - {name} kept failing "
                              f"({fail_speech}).")
                    break
                continue
            fail_streak = 0
            messages.append({"role": "user",
                             "content": f"TOOL RESULT ({name}):\n{str(result)[:1800]}"})
            continue
        # final answer
        if "say" in (call or {}):
            answer = sanitize_final(str((call or {}).get("say", "")))
        else:
            answer = sanitize_final(raw)
        break

    if not answer:
        # last-chance rescue: one non-streaming attempt on the best route
        try:
            answer = sanitize_final(
                llm.chat(messages, temperature=0.4, timeout=25))
        except Exception:
            answer = ("The language pool is unstable right now and I couldn't "
                      "finish that reply. Give me a moment and try again.")

    memory.record_turn(text, answer, surface)
    emit({"type": "done", "text": answer})
    db.trace(turn_id, "total_agent", (time.time() - t0) * 1000, f"steps={step + 1}")
    bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": answer})
    return answer





