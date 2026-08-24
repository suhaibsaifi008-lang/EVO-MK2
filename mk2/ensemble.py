"""Phase 4: Deep Thought — parallel reasoning ensemble for hard questions.

Three independent passes run in parallel threads:
    analyst   — facts, mechanisms, numbers
    skeptic   — what's wrong/missing in the naive answer; counterexamples
    advisor   — practical recommendation for THIS user
A final merge pass synthesizes one superior answer. Every pass is
wall-clock bounded. Falls back to a single chat() call if any leg dies.
"""
import threading

from . import llm

TIMEOUT = 50


def _leg(role_prompt: str, question: str, out: dict, key: str) -> None:
    try:
        out[key] = llm.chat(
            [{"role": "system", "content": role_prompt},
             {"role": "user", "content": question[:4000]}],
            role="reasoning", temperature=0.3, timeout=TIMEOUT)
    except Exception as exc:
        out[key] = f"(leg unavailable: {str(exc)[:80]})"


def deep_thought(question: str) -> str:
    question = (question or "").strip()
    out: dict = {}
    legs = {
        "analyst": (
            "You are a rigorous analyst. Answer with the key facts, "
            "mechanisms and numbers. Be precise and concrete. Max 200 words."),
        "skeptic": (
            "You are a skeptical reviewer. For the question given, state the "
            "common naive answer's flaws, edge cases, common misconceptions "
            "and what most answers get wrong. Max 150 words."),
        "advisor": (
            "You are a pragmatic advisor. Give the practically-best "
            "recommendation or decision path for this question, with "
            "tradeoffs. Max 150 words."),
    }
    threads = [
        threading.Thread(target=_leg, args=(p, question, out, k),
                         daemon=True, name=f"mk2-dt-{k}")
        for k, p in legs.items()
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(TIMEOUT + 5)

    good = [f"{k.upper()}:\n{out[k]}" for k in ("analyst", "skeptic", "advisor")
            if k in out and not out[k].startswith("(leg unavailable")]
    if len(good) < 2:  # ensemble collapsed - honest single pass
        return llm.chat([{"role": "user", "content": question}],
                        temperature=0.4, timeout=TIMEOUT)

    merged = "\n\n".join(good)
    return llm.chat(
        [{"role": "system",
          "content": ("You are Deep Thought, merging three expert passes "
                      "(analysis, skepticism, advice) into ONE superior "
                      "answer. Resolve disagreements explicitly, keep the "
                      "skeptic's corrections, end with the practical bottom "
                      "line. No meta-commentary about the passes. "
                      "Max 350 words.")},
         {"role": "user", "content": f"Question: {question}\n\n{merged}"}],
        temperature=0.3, timeout=TIMEOUT)


# ------------------------------------------------------------------ tool

from .tools import tool  # noqa: E402


@tool("deep_thought", "Think hard about a difficult question using a multi-pass reasoning ensemble. Slower but much better.",
      {"question": {"type": "string"}}, permission="read")
def deep_thought_tool(question: str) -> dict:
    if not (question or "").strip():
        return {"ok": False, "speech": "Give me an actual question.", "data": {}}
    answer = deep_thought(question)
    return {"ok": True, "speech": answer[:900], "data": {"answer": answer}}
