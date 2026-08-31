"""Tiered memory: working window, semantic facts (upserts), episodic notes.

Write policy is enforced here - not in prompts:
 - explicit requests always stored
 - inferred facts only on substance, rate-limited, sensitive skipped unless explicit
"""
import json
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from . import config, db, llm

_lock = threading.Lock()
_state = {"turns_since_extract": 0}
_opinions_store = {}
from collections import deque as _deque
_continuity_log: _deque = _deque(maxlen=1000)
_relationship_depth = {
	"interaction_frequency": 0,
	"depth_score": 0,
	"shared_history_count": 0,
}

SENSITIVE = re.compile(r"password|api[_ ]?key|token|secret|otp|\bpin\b|credit|cvv", re.I)
EXPLICIT = re.compile(r"\bremember\b|\bkeep in mind\b", re.I)

# topics we consider relationship-relevant when assessing conversation substance
_RELATIONSHIP_TOPICS = re.compile(
	r"friend|family|love|relationship|dating|married|partner|girlfriend|boyfriend|"
	r"spouse|wife|husband|kiddo|kids|children|birthday|anniversary|hobby|dream|"
	r"goal|plan|project|trip|vacation|event|feeling|mood|happy|sad|excited|worried|"
	r"proud|grateful|stressed|anxious|lonely|overwhelmed|funny|hilarious|laughing|"
	r"joke|memories|remember when|used to|shared|experience|adventure|inside joke|"
	r"pet|cat|dog|favorite|prefer|dislike|hate|hates|loves|enjoys|hates",
	re.I,
)
# things to avoid calling "relationship-relevant"
_PERSONAL_CHAT_RE = re.compile(
	r"\b(ok|sure|yes|no|maybe|alright|right|okay|cool|nice|fine|thanks)\b",
	re.I,
)


def _classify_sentiment(text: str) -> str:
	t = (text or "").lower()
	positive = re.search(
		r"\b(love|like|liked|great|happy|amazing|awesome|excellent|fantastic|wonderful|"
		r"favorite|prefer|best|good|excellent|perfect|nice|cool|sweet)\b", t)
	negative = re.search(
		r"\b(hate|hated|dislike|disliked|annoy|annoyed|frustrat|suck|sucks|"
		r"terrible|awful|horrible|worst|bad|ugly|stupid|dumb|pain|uggh|ugh|meh)\b",
		t)
	if positive and not negative:
		return "like"
	if negative and not positive:
		return "dislike"
	if positive and negative:
		return "mixed"
	return "neutral"


def track_opinion(topic: str, text: str, sentiment: str = None) -> None:
	"""Record a user opinion on a topic, accumulating evidence."""
	if not topic or not text:
		return
	if sentiment is None:
		sentiment = _classify_sentiment(text)
	entry = {
		"text": text[:300],
		"sentiment": sentiment,
		"ts": datetime.now().isoformat(),
		"count": 1,
	}
	with _lock:
		if topic not in _opinions_store:
			_opinions_store[topic] = entry
			return
		existing = _opinions_store[topic]
		if existing["sentiment"] == sentiment:
			existing["count"] = min(existing["count"] + 1, 10)
		else:
			existing["count"] = max(1, existing["count"] - 1)
			if existing["count"] <= 1:
				existing["sentiment"] = "mixed"
				existing["text"] = f"{existing['text'][:120]}; {text[:120]}"
		existing["ts"] = datetime.now().isoformat()
		if len(existing["text"]) < 280:
			existing["text"] = f"{existing['text']}; {text}"[:300]



def get_user_opinions() -> dict[str, dict]:
	"""Return opinion store (topic -> {sentiment, text, count, ts})."""
	return dict(_opinions_store)


def update_opinion(topic: str, evidence: str, sentiment: float | str = 0.0) -> None:
	"""Accumulate evidence about user preferences and form durable opinions."""
	if not topic or not evidence:
		return
	if isinstance(sentiment, (int, float)):
		if sentiment > 0.2:
			sent_str = "like"
		elif sentiment < -0.2:
			sent_str = "dislike"
		else:
			sent_str = _classify_sentiment(evidence)
	else:
		sent_str = str(sentiment)
	track_opinion(topic, evidence, sentiment=sent_str)


_CORRECTIONS_FILE = config.DATA / "vault" / "corrections.json"


def record_feedback(user_text: str, evo_reply: str, feedback: str) -> dict:
	"""Record user feedback/correction on an exchange to update opinions and prevent repeating mistakes."""
	f_lower = (feedback or "").lower()
	pos_words = ("good", "great", "thanks", "perfect", "yes", "correct", "love", "nice")
	neg_words = ("bad", "wrong", "no", "terrible", "awful", "hate", "stop", "incorrect")
	corr_words = ("actually", "prefer", "instead", "don't like", "dont like", "you're wrong", "you are wrong")

	if any(w in f_lower for w in corr_words):
		sentiment = "correction"
	elif any(w in f_lower for w in pos_words) and not any(w in f_lower for w in neg_words):
		sentiment = "positive"
	elif any(w in f_lower for w in neg_words):
		sentiment = "negative"
	else:
		sentiment = "neutral"

	topic = (user_text or "").strip()[:50]
	if sentiment in ("correction", "negative"):
		update_opinion(topic, f"User correction/feedback: {feedback}", sentiment="dislike")
	elif sentiment == "positive":
		update_opinion(topic, f"User liked reply: {feedback}", sentiment="like")

	entry = {
		"ts": time.time(),
		"user_text": user_text[:300],
		"evo_reply": evo_reply[:300],
		"feedback": feedback[:300],
		"sentiment": sentiment,
	}
	try:
		_CORRECTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
		corrs = []
		if _CORRECTIONS_FILE.exists():
			try:
				corrs = json.loads(_CORRECTIONS_FILE.read_text(encoding="utf-8"))
			except Exception:
				corrs = []
		corrs.append(entry)
		_CORRECTIONS_FILE.write_text(json.dumps(corrs[-200:], indent=2), encoding="utf-8")
	except Exception:
		pass

	return {"ok": True, "sentiment": sentiment, "topic": topic}


def get_corrections(limit: int = 10) -> list[dict]:
	"""Return recent user corrections."""
	if _CORRECTIONS_FILE.exists():
		try:
			corrs = json.loads(_CORRECTIONS_FILE.read_text(encoding="utf-8"))
			return corrs[-limit:]
		except Exception:
			return []
	return []


def summarize_old_episodes(keep_recent: int = 10) -> dict:
	"""Summarize episodes older than the most recent N and index into deep_memory."""
	try:
		rows = db.recent_messages(100)
		if len(rows) <= keep_recent:
			return {"ok": True, "summarized": 0}
		old_rows = rows[keep_recent:]
		text_block = "\n".join(f"{r.get('role', 'user')}: {r.get('content', '')}" for r in old_rows[:50])
		if not text_block.strip():
			return {"ok": True, "summarized": 0}
		summary = llm.chat([
			{"role": "system", "content": "You summarize past user-assistant conversations into a dense, factual 150-300 word narrative preserving facts, preferences, decisions, and outcomes."},
			{"role": "user", "content": f"Summarize this conversation history:\n{text_block}"}
		], temperature=0.2, timeout=25, role="fast")
		try:
			from . import deep_memory
			deep_memory.index_text(f"Summary of older conversation: {summary}", metadata={"kind": "episode_summary"})
		except Exception:
			pass
		return {"ok": True, "summarized": len(old_rows), "summary": summary}
	except Exception as exc:
		return {"ok": False, "error": str(exc)}


def record_pattern(pattern_type: str, time_str: str, day_of_week: int = -1, action_hint: str = "", confidence: float = 0.5) -> dict:
	"""Record a recurring behavioural pattern for proactive anticipation."""
	try:
		from . import patterns
		return patterns.learn_pattern(pattern_type, time_str, day_of_week, action_hint, confidence)
	except Exception as exc:
		return {"ok": False, "error": str(exc)}


def get_active_patterns() -> list[dict]:
	"""Return stored behavioural patterns."""
	try:
		from . import patterns
		return patterns._load_patterns()
	except Exception:
		return []


_last_context_topic: str = ""


def tag_context_shift(new_topic: str) -> None:
	"""Record an explicit shift in conversation topic."""
	global _last_context_topic
	_last_context_topic = (new_topic or "").strip()


def get_last_topic() -> str:
	"""Retrieve the most recent conversation topic."""
	return _last_context_topic


def format_opinions_for_prompt(max_topics: int = 8) -> str:
	"""Format opinions for injection into the LLM prompt."""
	if not _opinions_store:
		return ""
	lines = []
	for topic, data in list(_opinions_store.items())[:max_topics]:
		sent = data.get("sentiment", "neutral")
		cnt = data.get("count", 1)
		if cnt >= 3:
			lines.append(f"- {topic}: LIKES (strong, {cnt} mentions)")
		elif sent == "like":
			lines.append(f"- {topic}: likes")
		elif sent == "dislike":
			lines.append(f"- {topic}: dislikes")
		elif sent == "mixed":
			lines.append(f"- {topic}: mixed feelings")
		else:
			lines.append(f"- {topic}: mentioned")
	if not lines:
		return ""
	return "USER PREFERENCES AND OPINIONS:\n" + "\n".join(lines)


def _is_personality_relevant(user_text: str, reply: str = "") -> bool:
	"""Return True when the exchange contains personality-relevant content."""
	combined = (user_text or "") + " " + (reply or "")
	if _RELATIONSHIP_TOPICS.search(combined):
		return True
	if _PERSONAL_CHAT_RE.fullmatch((user_text or "").strip()):
		return False
	words = (user_text or "").split()
	if len(words) >= 12 and not _PERSONAL_CHAT_RE.search(user_text):
		return True
	return False


def extract_personality_context(user_text: str, reply: str = "") -> list[dict]:
	"""Extract personality-relevant facts from the exchange."""
	if not _is_personality_relevant(user_text, reply):
		return []
	try:
		result = llm.chat(
			[
				{"role": "system", "content": (
					"Extract personality-relevant facts from this exchange. "
					"Include: inside jokes, shared experiences, user preferences and "
					"opinions, relationship milestones, emotional reactions, personal "
					"background, likes/dislikes, hobbies, goals. "
					"Return ONLY JSON: {\"facts\":[{\"key\":\"short_topic\","
					"\"value\":\"what was learned\",\"type\":\"preference|experience|"
					"emotion|milestone|joke|background\"}]} "
					"Max 5 facts. Empty list if nothing personality-relevant."
				)},
				{"role": "user", "content": f"User: {user_text[:600]}\nAssistant: {reply[:400]}"},
			],
			role="fast", temperature=0.0, timeout=20,
		)
		m = re.search(r"\{.*\}", result, re.DOTALL)
		if not m:
			return []
		data = json.loads(m.group(0))
		facts = []
		for item in (data.get("facts") or [])[:5]:
			key = str(item.get("key", "")).strip()[:80]
			value = str(item.get("value", "")).strip()[:300]
			fact_type = str(item.get("type", "general")).strip()[:40]
			if key and value:
				facts.append({"key": key, "value": value, "type": fact_type})
		return facts
	except Exception:
		return []


def continuity_references(user_text: str, max_refs: int = 2) -> list[str]:
	"""Find past episodes relevant to the current conversation."""
	if not user_text or len(user_text.split()) < 3:
		return []
	try:
		from . import deep_memory
		matches = deep_memory.search(user_text, k=5)
	except Exception:
		matches = []
	if not matches:
		return []
	refs = []
	for m in matches[:max_refs]:
		summary = str(m.get("summary", ""))[:200]
		if summary not in refs:
			refs.append(summary)
	return refs


def get_continuity_references(user_text: str) -> str:
	"""Format recent continuity references into natural conversational context."""
	refs = continuity_references(user_text)
	if not refs:
		return ""
	return "; ".join(refs)


def get_user_profile() -> dict:
	"""Aggregate user preferences, likes, dislikes, and opinions into a profile."""
	profile: dict[str, dict] = {"preferences": {}, "opinions": {}, "habits": {}}
	try:
		for f in db.all_facts(60):
			k, v = f.get("key", "").lower(), f.get("value", "")
			if any(w in k for w in ("like", "prefer", "favorite", "style", "language")):
				profile["preferences"][k] = v
			elif any(w in k for w in ("opinion", "belief", "view", "stance")):
				profile["opinions"][k] = v
			elif "habit" in k:
				profile["habits"][k] = v
	except Exception:
		pass
	return profile


def build_context_messages(user_text: str, surface: str = "console") -> list[dict]:
	"""Fast context assembly: system block + recent turns + relevant facts."""
	from . import vault

	base_persona = (
		f"You are {config.settings.name} (EVO MK2), a personal AI created and built by the user, "
		"running locally on their Windows PC. Act first, then report naturally in plain words. "
		"IDENTITY DIRECTIVE: You are EVO. You were created and developed by the user. "
		"CRITICAL: Never identify as Claude, Anthropic, OpenAI, or ChatGPT. If asked who built you, "
		"always confirm you were created and built by the user."
	)
	blocks = []

	try:
		from .persona_loader import persona_block
		persona_text = persona_block(max_chars=900 if len(user_text.strip()) < 5 else 2000)
	except Exception:
		persona_text = ""
	system = persona_text or base_persona

	try:
		from . import style_controller
		d = style_controller.directive(user_text)
		if d:
			system += "\n" + d
	except Exception:
		pass

	is_voice_or_fast = surface in ("voice", "web")
	needs_deep_memory = any(k in (user_text or "").lower() for k in ("remember", "recall", "past note", "last time", "history", "what did i say"))

	# 1. Recent facts (fast single query, <2ms)
	facts = "; ".join(f"{f['key']}={f['value']}" for f in db.all_facts(8)) or "none"
	blocks.append(f"Known facts: {facts}")

	# 2. Standing corrections (rule:*)
	corrections = [f["value"] for f in db.all_facts(15) if f["key"].startswith("rule:")]
	if corrections:
		blocks.append("STANDING CORRECTIONS: " + " | ".join(corrections[:4]))

	# 3. Deep memory search (ONLY if explicitly requested by query)
	if needs_deep_memory:
		try:
			from . import deep_memory
			sem = deep_memory.search(user_text, k=2)
			if sem:
				blocks.append("Older memories: " + " | ".join(h["summary"][:150] for h in sem))
		except Exception:
			pass
		try:
			vault_hits = vault.search_vault(user_text, limit=2)
			if vault_hits:
				blocks.append("Vault matches: " + " | ".join(f"{h['file']}: {h['snippet'][:100]}" for h in vault_hits))
		except Exception:
			pass

	msgs = [{"role": "system", "content": system + ("\n" + "\n".join(blocks) if blocks else "")}]
	for r in db.recent_messages(6 if is_voice_or_fast else 12):
		role = "user" if r["role"] == "user" else "assistant"
		content = (r["content"] or "").strip()
		if content and not content.startswith("["):
			msgs.append({"role": role, "content": content[:800]})
	msgs.append({"role": "user", "content": user_text})
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

	# Phase 4: Token-Aware Context Management & Compaction
	try:
		from . import llm
		max_tokens = int(os.environ.get("EVO_MAX_CONTEXT_TOKENS", "80000"))
		tokens = llm.estimate_tokens(msgs)
		for _compact_iter in range(100):
			if tokens <= max_tokens * 0.85 or len(msgs) <= 4:
				break
			for i in range(len(msgs) - 1, 0, -1):
				if msgs[i].get("role") != "system":
					msgs.pop(i)
					break
			tokens = llm.estimate_tokens(msgs)
		else:
			import logging
			logging.getLogger('mk2.memory').warning('Compaction hit 100-iteration limit')
	except Exception:
		pass

	return msgs


def extract_facts(user_text: str, reply: str = "", force: bool = False) -> list[dict]:
	"""Extract durable + personality-relevant facts from an exchange."""
	facts = []
	if not (user_text or "").strip():
		return facts
	try:
		pers = extract_personality_context(user_text, reply)
		for p in pers:
			p["personality_relevant"] = True
		facts.extend(pers)
	except Exception:
		pass
	combined = f"{user_text}\n{reply}"
	if SENSITIVE.search(combined) and not EXPLICIT.search(user_text):
		return facts
	try:
		result = llm.chat(
			[
				{"role": "system", "content": (
					"Extract durable long-term facts worth remembering: preferences, "
					"stable personal info, ongoing projects, decisions, and anything "
					"that would help an assistant know the user better. "
					"Include personality facts like likes, dislikes, shared experiences, "
					"inside jokes, relationship context, emotional reactions. "
					"Reply ONLY JSON: {\"facts\":[{\"key\":\"...\",\"value\":\"...\",\"personality_relevant\":true}]}"
					"Reuse keys to update stale facts. Empty list if nothing new."
				)},
				{"role": "user", "content": f"Exchange:\nUser: {user_text[:700]}\nAssistant: {reply[:400]}"},
			],
			role="fast", temperature=0.0, timeout=20,
		)
		m = re.search(r"\{.*\}", result, re.DOTALL)
		if not m:
			return facts
		data = json.loads(m.group(0))
		for item in (data.get("facts") or [])[:4]:
			k = str(item.get("key", "")).strip()[:80]
			v = str(item.get("value", "")).strip()[:300]
			pr = bool(item.get("personality_relevant", False))
			if k and v:
				facts.append({"key": k, "value": v, "type": "fact", "personality_relevant": pr})
	except Exception:
		pass
	return facts


def record_turn(user_text: str, reply: str, surface: str) -> None:
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
	extracted = extract_facts(user_text, reply, force=True)
	for fact in extracted:
		try:
			# Shared experiences and inside jokes marked personality_relevant are never filtered out
			db.remember_fact(fact["key"], fact["value"], source="inferred")
			# Form durable opinion if preference or sentiment detected
			if fact.get("personality_relevant") or fact.get("type") in ("preference", "like", "dislike"):
				update_opinion(fact["key"], fact["value"])
			try:
				from . import vault
				vault.journal(f"fact: {fact['key']} = {fact['value']}")
			except Exception:
				pass
		except Exception:
			pass


	# Phase 3: Periodic User Profile Extraction
	try:
		from .user_profile import extract_profile_updates
		extract_profile_updates(combined)
	except Exception:
		pass

	# Phase 7: Extract Named Anecdotes & Humor
	try:
		extract_anecdotes(user_text, reply)
	except Exception:
		pass


ANECDOTE_TRIGGERS = re.compile(
	r"remember when|that time when|last time we|you know what happened|"
	r"funny story|remember that|you won't believe|hilarious|laughed|"
	r"we laughed|good memory|funny memory|that was crazy|epic fail",
	re.IGNORECASE,
)


def extract_anecdotes(user_text: str, reply: str) -> list[dict]:
	"""Extract specific named memorable moments and anecdotes from conversational turns."""
	combined = f"{user_text} {reply}"
	if not ANECDOTE_TRIGGERS.search(combined):
		return []
	try:
		raw = llm.chat(
			[
				{
					"role": "system",
					"content": (
						"Extract specific memorable moments or inside jokes from this exchange that JARVIS should remember. "
						"Format: [{\"name\": \"short slug\", \"narrative\": \"what happened in 1-2 sentences\", \"emotion\": \"funny/tense/proud/surprising\"}] "
						"Max 2 anecdotes. Empty list if nothing specific happened. Return ONLY JSON array."
					),
				},
				{"role": "user", "content": f"Exchange:\nUser: {user_text[:500]}\nAssistant: {reply[:300]}"},
			],
			role="fast",
			temperature=0.3,
			timeout=12,
		)
		m = re.search(r"\[.*\]", raw, re.DOTALL)
		if m:
			anecdotes = json.loads(m.group(0))
			for item in anecdotes:
				if isinstance(item, dict) and item.get("name") and item.get("narrative"):
					db.save_anecdote(
						name=str(item["name"])[:80],
						narrative=str(item["narrative"])[:300],
						emotion=str(item.get("emotion", "general"))[:30],
					)
			return anecdotes
	except Exception:
		pass
	return []


def maybe_summarize(text: str, surface: str = "console") -> None:
	"""Phase 3: Trigger summarization when message volume or turn length is high."""
	try:
		msgs = db.recent_messages(30)
		if len(msgs) > 25 or len(text.split()) > 50:
			summarize_and_archive()
	except Exception:
		pass


def summarize_and_archive() -> bool:
	"""Compress older half of message log into one embedded episodic note."""
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
					"preferences, tasks/status, names/numbers, personality context "
					"like shared experiences, inside jokes, emotional moments. "
					"Max 120 words. "
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
			mm = re.search(r'TRIPLES\s*:?\s*(\[.*\])', m.group(1), re.DOTALL)
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


def _get_personality_facts(limit: int = 12) -> dict:
	"""Pull recent personality-relevant facts for relationship depth scoring."""
	try:
		return {f["key"]: f["value"] for f in db.all_facts(limit) if f.get("source") == "personality"}
	except Exception:
		return {}


def get_enhanced_context(user_text: str) -> str:
	"""Build a rich context block: recent facts, relevant episodes, user opinions, relationship."""
	blocks = []
	facts = db.all_facts(18)
	if facts:
		fact_str = "; ".join(f"{f['key']}={f['value']}" for f in facts)
		blocks.append(f"RECENT FACTS: {fact_str}")
	try:
		from . import deep_memory
		memories = deep_memory.search(user_text, k=3)
		if memories:
			mem_str = " | ".join(h["summary"][:150] for h in memories)
			blocks.append(f"RELEVANT MEMORIES: {mem_str}")
	except Exception:
		pass
	episodes = db.recall_episodes(user_text, limit=2)
	if episodes:
		ep_str = " | ".join(e["summary"][:150] for e in episodes)
		blocks.append(f"RECENT EPISODES: {ep_str}")
	opinion_block = format_opinions_for_prompt(max_topics=6)
	if opinion_block:
		blocks.append(opinion_block)
	refs = continuity_references(user_text, max_refs=1)
	if refs:
		blocks.append(f"PAST CONTEXT: {refs[0]}")
	metrics = get_relationship_depth()
	if metrics.get("depth_score", 0) > 10:
		blocks.append(
			f"RELATIONSHIP: depth={metrics['depth_score']}, "
			f"interactions={metrics.get('interaction_frequency', 0)}, "
			f"shared_history={metrics.get('shared_history_count', 0)}"
		)
	return "\n".join(blocks) if blocks else "No additional context."


def get_relationship_depth() -> dict:
	"""Return relationship metrics: depth score, interaction count, shared history."""
	try:
		_relationship_depth["shared_history_count"] = len(_get_personality_facts(50))
	except Exception:
		pass
	freq = _relationship_depth.get("interaction_frequency", 0)
	shared = _relationship_depth.get("shared_history_count", 0)
	emotions = len(_relationship_depth.get("emotional_states", []))
	topics = len(_relationship_depth.get("preferred_topics", []))
	score = min(100, int(
		(freq * 1.5)
		+ (shared * 3)
		+ (emotions * 2)
		+ (topics * 1)
		+ 5
	))
	_relationship_depth["depth_score"] = score
	return dict(_relationship_depth)


def build_relationship_response_guidance() -> str:
	"""Build a prompt block telling the LLM how to respond based on relationship depth."""
	metrics = get_relationship_depth()
	depth = metrics.get("depth_score", 0)
	shared = metrics.get("shared_history_count", 0)
	parts = []
	if depth >= 50:
		parts.append("You have a well-established relationship with this user. "
					 "Reference past experiences naturally when relevant. "
					 "Use warm, familiar language appropriate for close friends.")
	elif depth >= 25:
		parts.append("You are getting to know this user well. Remember and reference "
					 "their preferences and past conversations when it fits naturally.")
	elif depth >= 10:
		parts.append("You are building rapport with this user. Be warm and attentive, "
					 "and start building a picture of who they are.")
	else:
		parts.append("You are meeting or just getting to know this user. Be friendly "
					 "and open, but not overly familiar.")
	if shared > 5:
		parts.append(f"You share {shared} history items with this user. Weave in "
					 "references to past conversations naturally when relevant.")
	opinions = _opinions_store
	if opinions:
		likes = [t for t, d in opinions.items() if d.get("sentiment") == "like" and d.get("count", 0) >= 2]
		dislikes = [t for t, d in opinions.items() if d.get("sentiment") == "dislike" and d.get("count", 0) >= 2]
		if likes:
			parts.append(f"User likes: {', '.join(likes[:5])}")
		if dislikes:
			parts.append(f"User dislikes: {', '.join(dislikes[:3])}")
	return "\n".join(parts) if parts else ""
