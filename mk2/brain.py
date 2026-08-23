"""Orchestrator: one agent loop, tools, streaming events, fast-path intents."""
import json
import re
import time
import uuid
from datetime import datetime
from typing import Callable

from . import db, llm, memory, tools
from .bus import bus

MAX_STEPS = 6


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
    out = (text or "").strip()
    out = re.sub(r"^\s*TOOL RESULT.*$", "", out, flags=re.MULTILINE)
    m = re.search(r"\{.*\"say\".*\}", out, re.DOTALL)
    if m:
        try:
            inner = json_loads(m.group(0)).get("say")
            if inner:
                out = str(inner).strip()
        except Exception:
            pass
    return re.sub(r"\n{3,}", "\n\n", out)[:2000]


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

    instant = fast_command(text)
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
    manifest_text = "\n".join(
        f"- {t['name']}: {t['description']} args={t['args']}" for t in manifest
    )

    system_extra = (
        f"\nTOOLS (exact names only):\n{manifest_text}\n"
        'To act, reply ONLY {"tool":"name","args":{...}}. After tool results settle, '
        'reply ONLY {"say":"<final spoken answer>"} interpreting results naturally - '
        "never dump raw output, never mention internal steps or tool names to the user."
    )
    messages[0]["content"] += system_extra

    answer = ""
    fail_streak = 0
    last_fail_tool = None
    last_fail_speech = ""
    last_fail_tool = None
    for step in range(MAX_STEPS):
        if check_cancel():
            raise TurnCancelled()
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
        except llm.LLMUnavailable as exc:
            reply = f"My language core is unreachable right now ({str(exc)[:100]})."
            emit({"type": "error", "text": reply})
            emit({"type": "done", "text": reply})
            return reply
        raw = "".join(parts).strip()
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
        answer = "That needed more steps than I'm allowed - I stopped safely."

    memory.record_turn(text, answer, surface)
    emit({"type": "done", "text": answer})
    db.trace(turn_id, "total_agent", (time.time() - t0) * 1000, f"steps={step + 1}")
    bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": answer})
    return answer





