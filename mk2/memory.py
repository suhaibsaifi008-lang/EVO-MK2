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
    from . import vault

    base_persona = (
        f"You are {config.settings.name}, a personal assistant on the user's "
        "PC. Act first, then report naturally in plain words."
    )
    blocks = []

    # Phase 7: editable persona overrides the generic line
    try:
        from .persona_loader import persona_block

        persona_text = persona_block(max_chars=700)
    except Exception:
        persona_text = ""
    system = (persona_text or base_persona) + "\n" + base_persona

    # Phase 7: tone-aware reply shaping
    try:
        from . import style_controller

        d = style_controller.directive(user_text)
        if d:
            system += "\n" + d
    except Exception:
        pass

    facts = "; ".join(f"{f['key']}={f['value']}" for f in db.all_facts(18)) or "none"
    episodes = db.recall_episodes(user_text, limit=3) if len(user_text.split()) > 5 else []
    vault_hits = vault.search_vault(user_text, limit=3) if len(user_text.split()) > 3 else []
    notes_index = ", ".join(n["topic"] for n in vault.list_notes()[:12]) or ""
    blocks.append(f"Known facts: {facts}")
    if notes_index:
        blocks.append(f"Memory vault topics: {notes_index}")

    # Phase 7.6: standing corrections from past feedback must be obeyed
    corrections = [f["value"] for f in db.all_facts(40)
                   if f["key"].startswith("rule:")]
    if corrections:
        blocks.append("STANDING CORRECTIONS (from the user, obey exactly): "
                      + " | ".join(corrections[:6]))

    # semantic episodic recall (Phase 4)
    try:
        from . import deep_memory

        sem = deep_memory.search(user_text, k=3)
        if sem:
            blocks.append("Older memories that may matter here: " + " | ".join(
                h["summary"][:170] for h in sem))
    except Exception:
        pass
    if episodes:
        joined = " | ".join(e["summary"][:200] for e in episodes)
        blocks.append(f"Relevant past episodes: {joined}")

    # knowledge-graph triples relevant to this message
    try:
        words = [w for w in user_text.lower().split() if len(w) > 3][:6]
        rel = []
        for t in db.triples_all(150):
            blob = t["subject"] + " " + t["object"]
            if any(w in blob for w in words):
                rel.append(f"{t['subject']} -[{t['predicate']}]-> {t['object']}")
        if rel:
            blocks.append("Known relationships: " + "; ".join(rel[:6]))
    except Exception:
        pass

    if vault_hits:
        joined = " | ".join(f"{h['file']}: {h['snippet'][:120]}" for h in vault_hits)
        blocks.append(f"Vault excerpts matching this message: {joined}")
    msgs = [{"role": "system", "content": system + "\n" + "\n".join(blocks)}]
    for r in db.recent_messages(14):
        role = "user" if r["role"] == "user" else "assistant"
        content = (r["content"] or "").strip()
        if content and not content.startswith("["):
            msgs.append({"role": role, "content": content[:1200]})
    msgs.append({"role": "user", "content": user_text})
    # Recency reminder LAST - weak models obey the final instruction best
    try:
        from .persona_loader import truth_law

        law = truth_law()
    except Exception:
        law = "Never lie or invent facts."
    msgs.append({"role": "system",
                 "content": law + (" REMINDER: reply in natural spoken "
                                   "language. If you mention options/"
                                   "points/steps you MUST list them right "
                                   "there. No meta-talk about your answer.")})
    return msgs


def record_turn(user_text: str, reply: str, surface: str) -> None:
    # Phase 7: capture style feedback before anything else resets tone
    try:
        from . import style_controller

        style_controller.note_feedback(user_text)
    except Exception:
        pass
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
                try:
                    from . import vault

                    vault.journal(f"fact: {k} = {v}")
                except Exception:
                    pass
    except Exception:
        pass


def summarize_and_archive() -> bool:
    """Compress older half of message log into one embedded episodic note
    + extract knowledge-graph triples from the same pass (Phase 4)."""
    rows = db.recent_messages(40)
    if len(rows) < 24:
        return False
    older = rows[: len(rows) // 2]
    transcript = "\n".join(
        f"{'U' if r['role']=='user' else 'E'}: {(r['content'] or '')[:300]}" for r in older
    )
    try:
        raw = llm.chat(
            [
                {"role": "system", "content": (
                    "Compress the exchange into notes that let an assistant "
                    "continue later: decisions, unresolved questions, "
                    "preferences, tasks/status, names/numbers. Max 120 words. "
                    'Then ALSO output a line "TRIPLES:" followed by JSON '
                    '[["subject","predicate","object"], ...] for durable '
                    "facts about the user or world mentioned (max 6, empty "
                    "list if none). Example ending: TRIPLES: "
                    '[["user","prefers","mechanical keyboards"]]')},
                {"role": "user", "content": transcript},
            ],
            role="fast", temperature=0.2, timeout=30,
        )
    except Exception:
        return False
    summary, triples_json = raw, "[]"
    m = re.search(r"TRIPLES:\s*(\[[\s\S]*)$", raw)
    if m:
        summary = raw[:m.start()].strip()
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                triples_json = json.dumps(parsed)
        except Exception:
            mm = re.search(r"\[.*\]", m.group(1), re.DOTALL)
            if mm:
                try:
                    triples_json = json.dumps(json.loads(mm.group(0)))
                except Exception:
                    pass
    if not summary:
        return False
    from . import deep_memory

    started = older[0]["ts"]
    importance = min(3.0, 1.0 + len(summary) / 600)
    ep_id = deep_memory.remember(summary, importance, started_at=started)
    try:
        for tri in json.loads(triples_json)[:6]:
            if isinstance(tri, list) and len(tri) == 3 and all(str(x).strip() for x in tri):
                db.triple_add(str(tri[0]), str(tri[1]), str(tri[2]),
                              src="summarizer")
                if ep_id:
                    vault_note = f"{tri[0]} -[{tri[1]}]-> {tri[2]}"
                    try:
                        from . import vault

                        vault.journal(f"fact: {vault_note}")
                    except Exception:
                        pass
    except Exception:
        pass
    return True
