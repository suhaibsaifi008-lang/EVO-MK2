"""Hierarchical Swarm Autonomy for EVO MK2.

Orchestrates concurrent multi-agent swarms running DAG-based execution.
Sub-agents run concurrently in worker threads with isolated memory scratchpads,
report live progress events over the bus, and synthesize unified outcomes.
"""
import concurrent.futures
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from . import config, db, llm, tools
from .tools import tool
from .bus import bus

log = logging.getLogger("mk2.swarm")


@dataclass
class SwarmTask:
    id: str
    role: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed
    result: str = ""
    error: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_s: float = 0.0


@dataclass
class SwarmExecution:
    id: str
    objective: str
    tasks: dict[str, SwarmTask] = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | failed
    started_at: float = 0.0
    completed_at: float = 0.0
    final_synthesis: str = ""
    subagent_events: list[dict] = field(default_factory=list)


_active_swarms: dict[str, SwarmExecution] = {}
_swarm_lock = threading.Lock()


def get_swarm_execution(swarm_id: str) -> Optional[dict]:
    with _swarm_lock:
        sw = _active_swarms.get(swarm_id)
        if not sw:
            return None
        return {
            "id": sw.id,
            "objective": sw.objective,
            "status": sw.status,
            "started_at": sw.started_at,
            "completed_at": sw.completed_at,
            "final_synthesis": sw.final_synthesis,
            "tasks": {tid: asdict(t) for tid, t in sw.tasks.items()},
        }


def list_active_swarms() -> list[dict]:
    with _swarm_lock:
        return [
            {
                "id": sw.id,
                "objective": sw.objective,
                "status": sw.status,
                "task_count": len(sw.tasks),
                "done_count": sum(1 for t in sw.tasks.values() if t.status == "done"),
            }
            for sw in _active_swarms.values()
        ]


class SwarmSubAgent:
    """Isolated autonomous worker executing a single DAG node."""

    def __init__(self, swarm_id: str, task: SwarmTask) -> None:
        self.swarm_id = swarm_id
        self.task = task
        self.scratchpad: list[dict] = []

    def execute(self, dependency_results: dict[str, str]) -> str:
        self.task.status = "running"
        self.task.started_at = time.time()
        bus.publish("swarm.task.started", {
            "swarm_id": self.swarm_id,
            "task_id": self.task.id,
            "role": self.task.role,
            "objective": self.task.objective,
        })
        log.info("[Swarm %s] Sub-agent [%s: %s] started", self.swarm_id, self.task.id, self.task.role)

        dep_context = ""
        if dependency_results:
            dep_lines = [f"Prerequisite Output ({k}):\n{v}\n" for k, v in dependency_results.items()]
            dep_context = "\n".join(dep_lines)

        system_prompt = (
            f"You are an autonomous sub-agent specialized as: {self.task.role}.\n"
            f"Your specific objective: {self.task.objective}\n"
            "You have direct access to system tools. Use tools when necessary.\n"
            'To act, reply ONLY: {"tool": "<name>", "args": {...}}.\n'
            'When finished, reply ONLY: {"say": "<conclusive factual answer or report>"}.\n'
            "Be precise, dense, and objective."
        )

        manifest = tools.manifest()
        compact_tools = ", ".join(t["name"] for t in manifest[:40])
        system_prompt += f"\nAvailable tools: {compact_tools} (and tool_synthesize if new tool required)."

        user_content = f"Objective: {self.task.objective}"
        if dep_context:
            user_content += f"\n\nContext from prerequisites:\n{dep_context}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Multi-turn tool execution loop (up to 5 steps per subtask)
        result_text = ""
        for step in range(5):
            try:
                raw = llm.chat(messages, temperature=0.2, timeout=25, role="fast")
                raw = (raw or "").strip()
                if not raw:
                    break

                # Parse tool or final response
                if raw.startswith("{") and raw.endswith("}"):
                    try:
                        call = json.loads(raw)
                    except Exception:
                        call = None
                else:
                    call = None

                if call and "tool" in call:
                    t_name = call["tool"]
                    t_args = call.get("args", {})
                    bus.publish("swarm.task.progress", {
                        "swarm_id": self.swarm_id,
                        "task_id": self.task.id,
                        "tool": t_name,
                    })
                    tool_res = tools.call(t_name, t_args)
                    res_speech = tool_res.get("speech", str(tool_res))
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": f"Tool result: {res_speech}"})
                    continue
                elif call and "say" in call:
                    result_text = str(call["say"])
                    break
                else:
                    result_text = raw
                    break
            except Exception as exc:
                log.warning("[Swarm %s] Step %d failed: %s", self.swarm_id, step, exc)
                result_text = f"Partial execution result: {exc}"
                break

        if not result_text:
            result_text = f"Subtask {self.task.id} completed with minimal output."

        self.task.completed_at = time.time()
        self.task.duration_s = round(self.task.completed_at - self.task.started_at, 2)
        self.task.status = "done"
        self.task.result = result_text

        bus.publish("swarm.task.completed", {
            "swarm_id": self.swarm_id,
            "task_id": self.task.id,
            "duration_s": self.task.duration_s,
            "summary": result_text[:120],
        })
        log.info("[Swarm %s] Sub-agent [%s] finished in %.1fs", self.swarm_id, self.task.id, self.task.duration_s)
        return result_text


class SwarmOrchestrator:
    """Decomposes objectives into a DAG and executes parallel sub-agent swarms."""

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max_workers

    def decompose_dag(self, objective: str) -> list[SwarmTask]:
        """Use LLM to decompose a complex ask into a parallel DAG of tasks with explicit dependencies."""
        prompt = (
            f"Objective: {objective}\n"
            "Decompose this into 2 to 4 parallel or sequential sub-agent tasks.\n"
            "Assign each task a distinct specialist role and declare dependencies where needed.\n"
            "Reply ONLY valid JSON in this exact structure:\n"
            "[\n"
            '  {"id": "t1", "role": "System Auditor", "objective": "...", "depends_on": []},\n'
            '  {"id": "t2", "role": "Researcher", "objective": "...", "depends_on": []},\n'
            '  {"id": "t3", "role": "Synthesizer", "objective": "...", "depends_on": ["t1", "t2"]}\n'
            "]"
        )
        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a swarm architect decomposing goals into parallel DAG workflows."},
                {"role": "user", "content": prompt},
            ], temperature=0.1, timeout=20, role="fast")

            # Extract JSON block
            raw = raw.strip()
            if "[" in raw and "]" in raw:
                start = raw.find("[")
                end = raw.rfind("]") + 1
                raw = raw[start:end]
            items = json.loads(raw)
            tasks = []
            for item in items:
                tasks.append(SwarmTask(
                    id=str(item.get("id", f"t{len(tasks)+1}")),
                    role=str(item.get("role", "Specialist")),
                    objective=str(item.get("objective", "")),
                    depends_on=list(item.get("depends_on", [])),
                ))
            if tasks:
                return tasks
        except Exception as exc:
            log.warning("DAG decomposition fallback: %s", exc)

        # Fallback 2-agent DAG
        return [
            SwarmTask(id="t1", role="Researcher", objective=f"Investigate core requirements for: {objective}", depends_on=[]),
            SwarmTask(id="t2", role="Synthesizer", objective=f"Consolidate and formulate complete solution for: {objective}", depends_on=["t1"]),
        ]

    def execute(self, objective: str, background: bool = True) -> dict[str, Any]:
        """Launch the swarm workflow."""
        swarm_id = f"sw_{int(time.time())}_{len(_active_swarms) + 1}"
        dag_tasks = self.decompose_dag(objective)
        execution = SwarmExecution(
            id=swarm_id,
            objective=objective,
            tasks={t.id: t for t in dag_tasks},
            status="running",
            started_at=time.time(),
        )

        with _swarm_lock:
            _active_swarms[swarm_id] = execution

        log.info("Dispatched Swarm %s with %d tasks for objective: '%s'", swarm_id, len(dag_tasks), objective)

        if background:
            t = threading.Thread(target=self._run_dag, args=(execution,), daemon=True, name=f"swarm-{swarm_id}")
            t.start()
            return {
                "ok": True,
                "speech": f"Swarm #{swarm_id} mobilized with {len(dag_tasks)} parallel sub-agents.",
                "data": {"swarm_id": swarm_id, "tasks": len(dag_tasks)},
            }
        else:
            self._run_dag(execution)
            return {
                "ok": execution.status == "done",
                "speech": execution.final_synthesis or f"Swarm #{swarm_id} completed.",
                "data": get_swarm_execution(swarm_id),
            }

    def _run_dag(self, execution: SwarmExecution) -> None:
        tasks = execution.tasks
        pending_ids = set(tasks.keys())
        running_futures: dict[concurrent.futures.Future, str] = {}
        completed_results: dict[str, str] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while pending_ids or running_futures:
                # 1. Schedule ready tasks whose dependencies are satisfied
                ready_to_schedule = []
                for tid in list(pending_ids):
                    t = tasks[tid]
                    if all(dep in completed_results for dep in t.depends_on):
                        ready_to_schedule.append(tid)

                for tid in ready_to_schedule:
                    pending_ids.remove(tid)
                    t = tasks[tid]
                    dep_data = {dep: completed_results[dep] for dep in t.depends_on if dep in completed_results}
                    agent = SwarmSubAgent(execution.id, t)
                    future = pool.submit(agent.execute, dep_data)
                    running_futures[future] = tid

                if not running_futures and pending_ids:
                    # Deadlock detection: circular dependency or missing requirement
                    log.error("[Swarm %s] Deadlock detected in DAG. Aborting remaining pending tasks.", execution.id)
                    for tid in pending_ids:
                        tasks[tid].status = "failed"
                        tasks[tid].error = "Deadlocked dependencies"
                    break

                # 2. Wait for at least one running task to finish
                done, _ = concurrent.futures.wait(
                    running_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for future in done:
                    tid = running_futures.pop(future)
                    try:
                        res = future.result()
                        completed_results[tid] = res
                    except Exception as exc:
                        log.error("[Swarm %s] Task %s raised exception: %s", execution.id, tid, exc)
                        tasks[tid].status = "failed"
                        tasks[tid].error = str(exc)
                        completed_results[tid] = f"Error: {exc}"

        # 3. Final Synthesis / Reduction Step
        execution.completed_at = time.time()
        duration = round(execution.completed_at - execution.started_at, 2)
        execution.status = "done"

        synthesis_prompt = (
            f"Overall Swarm Objective: {execution.objective}\n\n"
            "Sub-Agent Outcomes:\n"
        )
        for tid, t in tasks.items():
            synthesis_prompt += f"[{t.role} ({t.id}) - Status: {t.status}]:\n{t.result}\n\n"

        synthesis_prompt += "Synthesize these findings into a unified, definitive executive summary and resolution."

        try:
            summary = llm.chat([
                {"role": "system", "content": "You are the JARVIS swarm coordinator delivering a unified mission report."},
                {"role": "user", "content": synthesis_prompt},
            ], temperature=0.3, timeout=30, role="reasoning")
            execution.final_synthesis = (summary or "").strip()
        except Exception as exc:
            execution.final_synthesis = f"Swarm finished in {duration}s. All subtasks completed."

        bus.publish("swarm.completed", {
            "swarm_id": execution.id,
            "duration_s": duration,
            "summary": execution.final_synthesis[:200],
        })
        log.info("Swarm %s completed in %.1fs. Unified synthesis ready.", execution.id, duration)


_orchestrator: Optional[SwarmOrchestrator] = None


def get_swarm_orchestrator() -> SwarmOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SwarmOrchestrator(max_workers=4)
    return _orchestrator


# ---------------- Built-in Swarm Tools ----------------

@tool(
    name="swarm_dispatch",
    description="Mobilize a hierarchical swarm of parallel sub-agents to concurrently solve a complex objective.",
    args={
        "objective": {"type": "string", "description": "High-level goal to decompose and solve with parallel sub-agents"},
        "background": {"type": "boolean", "description": "Whether to run asynchronously in the background (default true)"},
    },
    permission="execute",
)
def swarm_dispatch(objective: str, background: bool = True) -> dict:
    orc = get_swarm_orchestrator()
    return orc.execute(objective, background=background)


@tool(
    name="swarm_status",
    description="Query telemetry and live status of a running or completed swarm execution.",
    args={"swarm_id": {"type": "string", "description": "The swarm identifier (e.g. sw_172000000_1)"}},
    permission="read",
)
def swarm_status(swarm_id: str) -> dict:
    info = get_swarm_execution(swarm_id)
    if not info:
        return {"ok": False, "speech": f"No active or recorded swarm with ID '{swarm_id}'.", "data": {}}
    tasks_done = sum(1 for t in info["tasks"].values() if t["status"] == "done")
    total = len(info["tasks"])
    speech = f"Swarm #{swarm_id} is {info['status']} ({tasks_done}/{total} sub-tasks finished)."
    if info.get("final_synthesis"):
        speech += f" Result: {info['final_synthesis'][:180]}"
    return {"ok": True, "speech": speech, "data": info}
