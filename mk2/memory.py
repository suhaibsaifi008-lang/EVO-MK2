"""Tiered memory: working window, semantic facts (upserts), episodic notes.

Write policy is enforced here - not in prompts:
  - explicit requests always stored
  - inferred facts only on substance, rate-limited, sensitive skipped unless explicit
"""
import json
import re
import threading
import time

from . import config, db, llm

_lock = threading.Lock()
_state = {"turns_since_extract": 0}

SENSITIVE = re.compile(r"password|api[_ ]?key|token|secret|otp|\bpin\b|credit|cvv", re.I)
EXPLICIT = re.compile(r"\bremember\b|\bkeep in mind\b", re.I)


def build_context_messages(user_text: str, surface: str = "console") -> list[dict]:
    """System block (persona+state) + recent verbatim turns + current input."""
    persona = (
        f"You are {config.settings.name}, a warm, sharp personal assistant on the user's PC. "
        f"Address them as '{config.settings.user_address}' only when natural. "
        "Match their tone; concise by default; honest about uncertainty; never recite "
        "capability lists; act first, then report naturally in plain words."
    )
    facts = "; ".join(f"{f['key']}={f['value']}" for f in db.all_facts(18)) or "none"
    episodes = db.recall_episodes(user_text, limit=3)
    blocks = [f"Known facts: {facts}"]
    if episodes:
        joined = " | ".join(e["summary"][:200] for e in episodes)
        blocks.append(f"Relevant past episodes: {joined}")
    msgs = [{"role": "system", "content": persona + "\n" + "\n".join(blocks)}]
    for r in db.recent_messages(14):
        role = "user" if r["role"] == "user" else "assistant"
        content = (r["content"] or "").strip()
        if content and not content.startswith("["):
            msgs.append({"role": role, "content": content[:1200]})
    msgs.append({"role": "user", "content": user_text})
    return msgs


def record_turn(user_text: str, reply: str, surface: str) -> None:
    db.log_message("user", user_text.strip(), surface)
    db.log_message("assistant", reply.strip(), surface)
    with _lock:
        _state["turns_since_extract"] += 1
        due = EXPLICIT.search(user_text) or _state["turns_since_extract"] >= 6
        _state["turns_since_extract"] = 0 if due else _state["turns_since_extract"]
    if not due:
        return
    combined = f"{user_text}\n{reply}"
    if SENSITIVE.search(combined) and not EXPLICIT.search(user_text):
        return
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": (
                    'Extract durable long-term facts worth remembering (preferences, '
                    'stable personal info, ongoing projects, decisions). Never temporary '
                    'chatter. Reply ONLY JSON: {"facts":[{"key":"...","value":"..."}]} '
                    "Reuse keys to update stale facts. Empty list if nothing durable."
                )},
                {"role": "user", "content": f"Exchange:\nUser: {user_text[:700]}\nAssistant: {reply[:400]}"},
            ],
            role="fast", temperature=0.0, timeout=20,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return
        data = json.loads(m.group(0))
        for item in (data.get("facts") or [])[:4]:
            k = str(item.get("key", "")).strip()[:80]
            v = str(item.get("value", "")).strip()[:300]
            if k and v:
                db.remember_fact(k, v, source="inferred")
    except Exception:
        pass


def summarize_and_archive() -> bool:
    """Compress older half of message log into one episodic note."""
    rows = db.recent_messages(40)
    if len(rows) < 24:
        return False
    older = rows[: len(rows) // 2]
    transcript = "\n".join(
        f"{'U' if r['role']=='user' else 'E'}: {(r['content'] or '')[:300]}" for r in older
    )
    try:
        summary = llm.chat(
            [
                {"role": "system", "content": (
                    "Compress into bullet notes that let an assistant continue later: "
                    "decisions, unresolved questions, preferences, tasks/status, names/"
                    "numbers. Max 120 words."
                )},
                {"role": "user", "content": transcript},
            ],
            role="fast", temperature=0.2, timeout=30,
        )
    except Exception:
        return False
    started = older[0]["ts"]
    importance = min(2.0, 1.0 + len(summary) / 600)
    db.add_episode(summary, started, importance)
    for r in older:
        pass  # messages stay (rolling prune handles volume); episodes are index
    return True
