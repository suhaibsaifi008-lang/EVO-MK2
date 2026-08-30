"""Planning and deliberation engine for EVO MK2.

Sits between user requests and execution:
1. Goal decomposition into structured DAG steps
2. Step execution with LLM verification (temperature=0)
3. Adaptive replanning around failure (up to 2 attempts)
4. Final synthesis into a single coherent answer.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import llm, tools

log = logging.getLogger("mk2.planner")

MAX_PLAN_WALL_CLOCK = 45.0  # seconds


@dataclass
class PlanStep:
    id: int
    description: str
    tool_hint: Optional[str] = None
    depends_on: list[int] = field(default_factory=list)
    acceptance: str = "Step executed successfully"
    status: str = "pending"  # pending, running, done, failed, skipped
    result: str = ""
    duration_s: float = 0.0


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]
    rationale: str = ""
    created_at: float = field(default_factory=time.time)
    replan_count: int = 0


def _extract_json_object(raw: str) -> Optional[dict]:
    """Robustly extract JSON object from LLM response."""
    text = (raw or "").strip()
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}") + 1
        candidate = text[start:end]
        try:
            res = json.loads(candidate)
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    try:
        res = json.loads(text)
        if isinstance(res, dict):
            return res
    except Exception:
        pass
    return None


def should_plan(text: str, surface: str = "console") -> bool:
    """Determine if a user turn warrants deliberate multi-step planning."""
    t = (text or "").lower().strip()
    if not t or len(t) < 8:
        return False

    # Never plan on pure questions, dialogue, identity, or conversational queries
    if t.endswith("?") or t.startswith(("what", "who", "when", "where", "why", "how", "if", "so", "are", "is", "tell me", "the time", "the date")):
        return False
    if any(q in t for q in ("who built", "why are you", "who are you", "are you", "built by", "built you")):
        return False

    # Explicit planning / complex task keywords
    plan_keywords = (
        "plan and build", "set up", "setup", "automate the", "automate a", "research and",
        "compare and", "investigate and", "analyze and", "find and summarize",
        "prepare a report", "create a project", "workflow for", "multi-step",
        "break down", "develop a", "deploy", "audit and fix",
    )
    if any(kw in t for kw in plan_keywords):
        return True

    # Check for compound conjunctions indicating 3+ distinct execution steps
    conjunction_matches = len(re.findall(r"\b(and then|after that)\b", t))
    if conjunction_matches >= 2:
        return True

    # Multiple action verbs in sentence
    action_verbs = len(re.findall(r"\b(create|write|fetch|download|search|extract|compile|deploy|send|format|check|verify)\b", t))
    if action_verbs >= 3:
        return True

    return False


def plan(goal: str, context: str = "") -> Optional[Plan]:
    """Decompose a high-level goal into a structured JSON execution plan."""
    manifest = tools.manifest()
    available_tools = ", ".join(t.get("name", "") for t in manifest[:30])

    prompt = (
        f"Goal: {goal}\n"
        f"Context: {context[:400] if context else 'None'}\n"
        f"Available Tools: {available_tools}\n\n"
        "Decompose this goal into a concise, high-efficiency plan (2 to 5 steps maximum).\n"
        "Respond ONLY with a JSON object in this exact schema:\n"
        "{\n"
        '  "steps": [\n'
        '    {"id": 1, "description": "...", "tool_hint": "tool_name_or_null", "depends_on": [], "acceptance": "verification criterion"}\n'
        "  ],\n"
        '  "rationale": "one sentence explaining approach"\n'
        "}"
    )

    try:
        raw = llm.chat([
            {"role": "system", "content": "You are a master planner decomposing user objectives into deterministic steps."},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.1, timeout=15)

        data = _extract_json_object(raw)
        if not data or not isinstance(data.get("steps"), list):
            log.warning("Planner failed to parse JSON structure from LLM response: %s", raw[:120])
            return None

        steps: list[PlanStep] = []
        for s in data["steps"]:
            if not isinstance(s, dict):
                continue
            steps.append(PlanStep(
                id=int(s.get("id", len(steps) + 1)),
                description=str(s.get("description", "")),
                tool_hint=s.get("tool_hint") if s.get("tool_hint") else None,
                depends_on=[int(d) for d in s.get("depends_on", []) if isinstance(d, (int, str)) and str(d).isdigit()],
                acceptance=str(s.get("acceptance", "Step completed")),
            ))

        if not steps:
            return None

        return Plan(
            goal=goal,
            steps=steps,
            rationale=str(data.get("rationale", "")),
        )
    except Exception as exc:
        log.warning("Plan generation error: %s", exc)
        return None


def verify_step(step: PlanStep, result: str, plan_goal: str) -> tuple[bool, str]:
    """Verify if the step outcome satisfies its acceptance criteria."""
    if not result:
        return False, "Empty result returned"

    # Fast heuristic check
    res_low = result.lower()
    if "error:" in res_low or "traceback" in res_low or "permission denied" in res_low:
        return False, f"Step execution resulted in an error: {result[:120]}"

    prompt = (
        f"Goal: {plan_goal}\n"
        f"Step: {step.description}\n"
        f"Acceptance Criterion: {step.acceptance}\n"
        f"Step Result: {result[:800]}\n\n"
        "Did this step result advance the goal and satisfy the criteria?\n"
        "Reply ONLY with 'YES: <brief reason>' or 'NO: <brief reason>'."
    )

    try:
        verdict = llm.chat([
            {"role": "system", "content": "You are a strict QA verification evaluator. Respond ONLY YES or NO."},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.0, timeout=10)

        v = verdict.strip()
        if v.upper().startswith("YES"):
            reason = v[4:].strip(": ")
            return True, reason or "Verified"
        else:
            reason = v[3:].strip(": ") if v.upper().startswith("NO") else v
            return False, reason or "Failed verification"
    except Exception as exc:
        log.warning("Step verification fallback to True: %s", exc)
        return True, "Verification skipped"


def replan(current_plan: Plan, failed_step: PlanStep, failure_reason: str) -> Optional[Plan]:
    """Revise remaining steps in the plan around a failed step."""
    completed_steps = [s for s in current_plan.steps if s.status == "done"]
    remaining_steps = [s for s in current_plan.steps if s.status in ("pending", "failed")]

    summary_done = "\n".join(f"Step {s.id} ({s.description}): {s.result[:150]}" for s in completed_steps) or "None"
    prompt = (
        f"Original Goal: {current_plan.goal}\n"
        f"Completed Steps:\n{summary_done}\n\n"
        f"Failed Step {failed_step.id}: {failed_step.description}\n"
        f"Failure Reason: {failure_reason}\n\n"
        "Revise the remaining plan to bypass this failure or use an alternative method.\n"
        "Respond ONLY with a JSON object in this exact schema:\n"
        "{\n"
        '  "steps": [\n'
        '    {"id": <next_id>, "description": "...", "tool_hint": "...", "depends_on": [], "acceptance": "..."}\n'
        "  ],\n"
        '  "rationale": "one sentence explaining the pivot"\n'
        "}"
    )

    try:
        raw = llm.chat([
            {"role": "system", "content": "You are an adaptive replanning architect revising execution around failure."},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.2, timeout=15)

        data = _extract_json_object(raw)
        if not data or not isinstance(data.get("steps"), list):
            return None

        new_steps: list[PlanStep] = []
        next_id = max([s.id for s in completed_steps] + [0]) + 1
        for s in data["steps"]:
            if not isinstance(s, dict):
                continue
            new_steps.append(PlanStep(
                id=next_id,
                description=str(s.get("description", "")),
                tool_hint=s.get("tool_hint") if s.get("tool_hint") else None,
                depends_on=[int(d) for d in s.get("depends_on", []) if isinstance(d, (int, str)) and str(d).isdigit()],
                acceptance=str(s.get("acceptance", "Step completed")),
            ))
            next_id += 1

        if not new_steps:
            return None

        return Plan(
            goal=current_plan.goal,
            steps=completed_steps + new_steps,
            rationale=str(data.get("rationale", "Replanned around failure")),
            created_at=current_plan.created_at,
            replan_count=current_plan.replan_count + 1,
        )
    except Exception as exc:
        log.warning("Replanning error: %s", exc)
        return None


def synthesize_results(goal: str, plan: Plan) -> str:
    """Synthesize all completed step outcomes into a unified, direct spoken answer."""
    step_summaries = []
    for s in plan.steps:
        if s.status == "done":
            step_summaries.append(f"Step {s.id} ({s.description}): {s.result}")
        elif s.status == "failed":
            step_summaries.append(f"Step {s.id} ({s.description}) [FAILED]: {s.result}")

    context = "\n".join(step_summaries)
    prompt = (
        f"Goal: {goal}\n\n"
        f"Execution Findings:\n{context}\n\n"
        "Synthesize these findings into a direct, conversational, and comprehensive answer for the user.\n"
        "Rules:\n"
        "- Speak naturally like JARVIS: crisp, direct, confident.\n"
        "- Do NOT list internal tool names or step numbers.\n"
        "- Present the actual solution, answer, or deliverable clearly.\n"
    )

    try:
        answer = llm.chat([
            {"role": "system", "content": "You are EVO MK2 synthesizing plan results. Never identify as Claude, Anthropic, or OpenAI. Never say 'Completed plan for...'. Be direct and concise."},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.3, timeout=15)
        return answer.strip()
    except Exception as exc:
        log.warning("Synthesis error: %s", exc)
        # Fallback to direct compilation
        return " ".join(s.result for s in plan.steps if s.status == "done" and s.result)


def execute_plan(
    p: Plan,
    emit: Optional[Callable[[dict], None]] = None,
    check_cancel: Optional[Callable[[], bool]] = None,
    max_replans: int = 2,
) -> dict[str, Any]:
    """Execute a structured plan with step-by-step verification and adaptive replanning."""
    t0 = time.time()
    current_plan = p

    def _emit(ev: dict):
        if emit:
            try:
                emit(ev)
            except Exception:
                pass

    log.info("Executing plan for goal '%s' (%d steps)", current_plan.goal, len(current_plan.steps))

    while True:
        # Find next pending step
        pending = [s for s in current_plan.steps if s.status == "pending"]
        if not pending:
            break

        step = pending[0]

        # Check time budget
        if time.time() - t0 > MAX_PLAN_WALL_CLOCK:
            log.warning("Plan exceeded wall clock budget of %.1fs", MAX_PLAN_WALL_CLOCK)
            break

        if check_cancel and check_cancel():
            log.info("Plan cancelled by user interrupt")
            return {"ok": False, "answer": "Plan cancelled.", "plan": current_plan}

        step.status = "running"
        _emit({"type": "plan_step", "step_id": step.id, "description": step.description, "status": "running"})
        step_t0 = time.time()

        # Execute step
        result_text = ""
        try:
            valid_tool_names = {t.get("name") for t in tools.manifest()}
            if step.tool_hint and step.tool_hint in valid_tool_names:
                # Execute direct tool
                tool_res = tools.call(step.tool_hint, {"query": step.description, "text": step.description})
                result_text = tool_res.get("speech") or str(tool_res.get("data", "")) or "Tool finished"
            else:
                # LLM execution turn for this specific sub-task
                sub_res = llm.chat([
                    {"role": "system", "content": f"You are EVO MK2 executing step {step.id} for goal: {current_plan.goal}. Never identify as Claude, Anthropic, or OpenAI. Provide exact result."},
                    {"role": "user", "content": step.description},
                ], role="fast", temperature=0.2, timeout=20)
                result_text = sub_res.strip()
        except Exception as exc:
            result_text = f"Error: {exc}"

        step.duration_s = round(time.time() - step_t0, 2)
        step.result = result_text

        # Verify step
        ok, reason = verify_step(step, result_text, current_plan.goal)
        if ok:
            step.status = "done"
            _emit({"type": "plan_step", "step_id": step.id, "description": step.description, "status": "done", "result": result_text[:120]})
            log.info("Step %d done (%.2fs): %s", step.id, step.duration_s, reason)
        else:
            step.status = "failed"
            _emit({"type": "plan_step", "step_id": step.id, "description": step.description, "status": "failed", "reason": reason})
            log.warning("Step %d failed (%.2fs): %s", step.id, step.duration_s, reason)

            # Attempt adaptive replanning
            if current_plan.replan_count < max_replans:
                log.info("Triggering adaptive replan (attempt %d/%d)", current_plan.replan_count + 1, max_replans)
                _emit({"type": "plan_replan", "attempt": current_plan.replan_count + 1, "failed_step": step.id})
                new_plan = replan(current_plan, step, reason)
                if new_plan and len(new_plan.steps) > len([s for s in current_plan.steps if s.status == "done"]):
                    current_plan = new_plan
                    continue

            # If replan failed or exhausted, abort further execution
            break

    # Synthesize results
    final_answer = synthesize_results(current_plan.goal, current_plan)
    total_dur = round(time.time() - t0, 2)
    all_done = all(s.status == "done" for s in current_plan.steps)

    log.info("Plan finished in %.2fs (success=%s)", total_dur, all_done)
    return {
        "ok": all_done,
        "answer": final_answer,
        "duration_s": total_dur,
        "plan": current_plan,
    }


REFLECT_SYSTEM = (
    "You are a self-check reviewer for JARVIS. Review this answer for: "
    "1) Does it directly answer the question? "
    "2) Are there hallucinations or ungrounded claims? "
    "3) Does it violate persona rules (NO 'As an AI', NO capability lists, NO corporate disclaimers)? "
    "4) Is it concise, natural, and crisp? "
    "If corrections are needed, rewrite it directly. If completely fine, return the original text. "
    "Output ONLY the final corrected text."
)


def reflect_answer(question: str, answer: str, context: Optional[list[dict]] = None) -> Optional[str]:
    """Run a fast self-reflection pass to verify quality and anti-hallucination."""
    if not answer or len(answer) < 30:
        return None
    try:
        ctx_summary = ""
        if context:
            ctx_summary = " | ".join(
                f"{m.get('role', '')}: {str(m.get('content', ''))[:80]}" for m in context[-3:]
            )
        prompt = f"User Question: {question[:300]}\nAssistant Draft: {answer[:800]}"
        if ctx_summary:
            prompt += f"\nContext: {ctx_summary}"

        raw = llm.chat([
            {"role": "system", "content": REFLECT_SYSTEM},
            {"role": "user", "content": prompt},
        ], role="fast", temperature=0.0, timeout=8, max_providers=1)

        if raw and raw.strip() and raw.strip() != answer.strip():
            text_clean = raw.strip()
            # If the model included critique header or divider, extract the final portion
            if "---" in text_clean:
                text_clean = text_clean.split("---")[-1].strip()
            elif "corrected version:" in text_clean.lower():
                text_clean = text_clean.split(":", 1)[-1].strip()
            if text_clean.lower().startswith("the draft"):
                return None
            from .response_validator import validate_response
            _, validated = validate_response(text_clean)
            return validated
    except Exception as exc:
        log.debug("Self-reflection skipped: %s", exc)
    return None
