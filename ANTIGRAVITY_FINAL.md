# ANTIGRAVITY — EVO MK2 Final Push to 9/10 + Unified Intelligence
## Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
## Start: git checkout -b jarvis-9-final

You are the final hardening agent. Previous passes built the money modules,
wired barge-in, built deep research, and fixed most bugs.
This pass fixes the remaining infrastructure AND adds unified intelligence
that can learn, connect, and reason about ANY topic.

Score target: 9/10.

---

## PART 1: FIX CONVO.PY BLOCKING (CRITICAL)

File: mk2/voice/convo.py, line 137

Problem: self.pipeline.process_utterance(text) runs inside the main audio
loop. While the LLM generates (2-5s), the entire loop blocks — mic stops,
barge-in stops, VAD stops. User cannot interrupt. Voice is broken.

Fix: Run process_utterance in a separate daemon thread. Main loop never
blocks on LLM or TTS.

Implementation:
 - Keep the _reader thread for mic frames (already exists)
 - Add a _process thread for LLM + TTS
 - Use a queue to signal completion back to main loop
 - Barge-in processes every frame regardless of LLM state
 - If pipeline fails, fall back to speaker.say()

Key constraints:
 - Main loop NEVER blocks on LLM or TTS
 - Barge-in fires even while LLM is generating
 - Daemon threads (die with main thread, no orphan processes)
 - No bare except: pass

---

## PART 2: ADD 5 MONEY API ENDPOINTS

File: mk2/server.py
Add after existing autonomy endpoints:

GET /api/money/briefing — today's money briefing
GET /api/money/pipeline — funnel + leads + pending
GET /api/money/clients — CRM client list with scores
GET /api/money/opportunities — scored gigs
POST /api/money/followup/{client_id} — generate follow-up draft

Each endpoint: try/except, return {"ok": False, "error": ...} on failure.
Add permission="execute" check.

---

## PART 3: UNIFIED INTELLIGENCE — THE BRAIN THAT UNDERSTANDS MONEY

### 3a. Build MoneyIntelligence context injector

File: mk2/money_intelligence.py (NEW)

Build a class that creates a rich money context string for the LLM.
This is NOT tool calls — it's raw data the LLM reasons about directly.

class MoneyIntelligence:
 def __init__(self):
 self.crm = get_crm()
 self.revenue = get_revenue_tracker()
 self.scorer = get_opportunity_scorer()
 self.briefing = get_money_briefing_engine()

 def get_context(self) -> str:
 Return a COMPLETE money snapshot:
 - 30-day revenue + month-over-month change
 - Pipeline value (active lead budgets * win probability)
 - Active clients by stage with lead scores
 - Best performing gig types (from CRM tags + revenue)
 - Response rate, win rate, average pay
 - Top 3 actions for today
 - Upcoming deliverables and payment due dates
 - Single highest-EV opportunity right now

 Format as clean text the LLM can read and reason about.
 This becomes the LLM's "knowledge" of the business.

### 3b. Wire into brain.py

File: mk2/brain.py, around line 525 (money keyword detection)

Replace the basic number injection with MoneyIntelligence context:
 from .money_intelligence import get_money_intelligence
 mi = get_money_intelligence()
 money_ctx = mi.get_context()
 system_extra = f"\n=== YOUR BUSINESS ===\n{money_ctx}\n=== END ===\n"
 system_extra += (
 "\nYou understand this business deeply. Give specific recommendations with "
 "numbers. Don't call tools for analysis — you already have everything you need. "
 "Only call tools for actions: sending proposals, creating invoices, recording payments."
 )

Result: LLM says "Your response rate dropped this week — your winning proposals
average 89 words, try shortening them" instead of calling get_response_rate()
and reading the number back.

---

## PART 4: CROSS-DOMAIN KNOWLEDGE — LEARN ANYTHING AND CONNECT IT

### 4a. Build KnowledgeSynthesizer

File: mk2/knowledge.py (NEW — extends the existing RAG/knowledge system)

class KnowledgeSynthesizer:
 """Connects new knowledge to existing knowledge automatically."""

 def __init__(self):
 self.rag = get_rag() if available else None

 def on_new_research(self, topic: str, content: str) -> dict:
 """Called after deep_research completes. Finds connections."""
 connections = []

 # 1. Search existing vault for related topics
 if self.rag:
 existing = self.rag.search(topic, top_k=5)
 for ex in existing:
 connections.append({
 "existing_topic": ex.get("title", ""),
 "relevance": ex.get("score", 0),
 "connection_type": "direct_match",
 })

 # 2. Ask LLM: what does this connect to?
 try:
 from .llm import chat
 prompt = f"""I just researched: "{topic}"

Here is a summary of what I learned:
{content[:2000]}

Based on this, what OTHER topics does this connect to?
List 3-5 connection topics that might be in my existing knowledge base.
Format as a simple list, one per line."""

 result = chat([
 {"role": "system", "content": "You are a knowledge management assistant. List related topics concisely."},
 {"role": "user", "content": prompt},
 ], role="fast", timeout=10)

 for line in (result or "").split("\n"):
 line = line.strip("- •").strip()
 if line:
 connections.append({
 "existing_topic": line,
 "relevance": 0.5,
 "connection_type": "llm_inferred",
 })
 except Exception:
 pass

 # 3. Save connections to knowledge graph
 return {
 "topic": topic,
 "connections": connections,
 "new_entries": len(connections),
 }

 def get_related(self, topic: str) -> list[dict]:
 """Get all knowledge connected to a topic."""
 related = []
 if self.rag:
 related.extend(self.rag.search(topic, top_k=10))
 return related

### 4b. Wire into deep_research

File: mk2/research_tools.py, in the deep_research function

After engine.research(topic) completes:
 from .knowledge import get_knowledge_synthesizer
 synth = get_knowledge_synthesizer()
 connections = synth.on_new_research(topic, report_obj.markdown_report)

 if connections.get("connections"):
 _emit(f"Connected to {connections['new_entries']} existing knowledge areas")
 # Store connections in the report for later retrieval
 report_obj.markdown_report += f"\n\n## Knowledge Connections\n"
 for c in connections["connections"]:
 report_obj.markdown_report += f"- {c['existing_topic']} ({c['connection_type']})\n"

### 4c. Wire into brain.py — proactive recall

File: mk2/brain.py

Before processing any user message, check if relevant knowledge exists:

 def _inject_relevant_knowledge(self, text: str, messages: list[dict]) -> None:
 """Before responding, check vault/rag for relevant prior knowledge."""
 try:
 from .knowledge import get_knowledge_synthesizer
 synth = get_knowledge_synthesizer()
 related = synth.get_related(text)
 if related:
 knowledge_block = "\n".join(
 f"[{i+1}] {r.get('title', '')}: {r.get('text', '')[:300]}"
 for i, r in enumerate(related[:3])
 )
 extra = f"\n=== RELEVANT KNOWLEDGE ===\n{knowledge_block}\n=== END ===\n"
 extra += "\nUse this knowledge if relevant. Reference it naturally."
 messages[0]["content"] += extra
 except Exception:
 pass

Call this before every LLM call in handle_turn(). The LLM now has access
to everything it has ever learned, automatically surfaced for relevance.

---

## PART 5: SKILL DISTILLATION — TURN KNOWLEDGE INTO ACTIONABLE SKILLS

### 5a. Build SkillExtractor

File: mk2/skills.py (NEW)

class SkillExtractor:
 """Extracts actionable procedures from research and conversation."""

 def __init__(self):
 self.skills_dir = DATA / "extracted_skills"
 self.skills_dir.mkdir(parents=True, exist_ok=True)

 def extract_from_research(self, topic: str, content: str) -> list[dict]:
 """After deep research, extract actionable skills."""
 try:
 from .llm import chat
 prompt = f"""From this research on "{topic}":

{content[:3000]}

Extract 3-5 ACTIONABLE PROCEDURES — things the user can actually DO.
Each procedure should be:
1. A clear action ("When X happens, do Y")
2. Specific enough to follow without additional research
3. Tested/verified (only include what the sources agree on)

Format as a numbered list. If no actionable procedures exist, say "None identified"."""

 result = chat([
 {"role": "system", "content": "You are a skill extraction specialist. Extract only actionable, verified procedures."},
 {"role": "user", "content": prompt},
 ], role="fast", timeout=15)

 skills = []
 for line in (result or "").split("\n"):
 line = line.strip()
 if line and not line.lower().startswith("none"):
 skills.append({
 "topic": topic,
 "procedure": line,
 "source": "deep_research",
 "confidence": "high",
 "uses": 0,
 "created_at": time.time(),
 })

 # Save to disk
 if skills:
 path = self.skills_dir / f"{_slug(topic)}.json"
 path.write_text(json.dumps(skills, indent=2), encoding="utf-8")

 return skills
 except Exception as exc:
 log.warning("Skill extraction failed: %s", exc)
 return []

 def get_relevant_skills(self, context: str) -> list[dict]:
 """Find skills relevant to current task."""
 relevant = []
 try:
 for f in self.skills_dir.glob("*.json"):
 skills = json.loads(f.read_text(encoding="utf-8"))
 for skill in skills:
 # Simple keyword match — upgrade to embedding similarity later
 topic_words = skill.get("topic", "").lower().split()
 ctx_words = context.lower().split()
 overlap = len(set(topic_words) & set(ctx_words))
 if overlap > 0:
 relevant.append(skill)
 except Exception:
 pass
 return relevant

### 5b. Wire into deep_research

File: mk2/research_tools.py

After knowledge synthesis:
 from .skills import get_skill_extractor
 extractor = get_skill_extractor()
 new_skills = extractor.extract_from_research(topic, report_obj.markdown_report)
 if new_skills:
 _emit(f"Extracted {len(new_skills)} actionable skills")
 for skill in new_skills:
 report_obj.markdown_report += f"\n- **Skill**: {skill['procedure']}\n"

### 5c. Wire into brain.py — inject relevant skills

File: mk2/brain.py

In _inject_relevant_knowledge(), also inject relevant skills:

 def _inject_relevant_knowledge(self, text, messages):
 # ... existing knowledge injection ...

 # Also inject relevant skills
 try:
 from .skills import get_skill_extractor
 extractor = get_skill_extractor()
 skills = extractor.get_relevant_skills(text)
 if skills:
 skill_block = "\n".join(
 f"- {s['procedure']}" for s in skills[:3]
 )
 messages[0]["content"] += (
 f"\n=== RELEVANT PROCEDURES ===\n{skill_block}\n"
 "Apply these procedures when relevant. Don't mention them unless the user asks."
 )
 except Exception:
 pass

Now when the user says "help me grow my YouTube channel" and the agent
previously researched YouTube, it automatically applies the extracted skills
("Use hooks in first 3 seconds", "Post at 2pm on weekdays") without being told.

---

## PART 6: PROACTIVE KNOWLEDGE APPLICATION

### 6a. Add pre-task knowledge query to brain

File: mk2/brain.py

Before ANY task (not just money), the brain should check what it knows:

In handle_turn(), before calling the LLM:
 1. Take the user's message
 2. Query knowledge base: "What do I know about this topic?"
 3. Query skills: "What procedures apply here?"
 4. Inject both into the LLM context

This is the same pattern as the money injection, but universal.

Implementation:
 - Move the knowledge+skill injection to a single method
 - Call it for ALL messages, not just money keywords
 - Keep it lightweight — max 3 knowledge entries, max 2 skills
 - Use role="fast" model for the relevance check (low latency)

### 6b. Autonomous learning triggers

The agent should proactively learn when it encounters unknowns:

In brain.py, after LLM responds:
 if response contains "I don't know" or "I'm not familiar" or similar:
 Trigger deep_research on the topic
 Save results to vault + knowledge graph + skills
 Reply: "I didn't know that well — I just researched it. Here's what I found..."

Implementation:
 - Detect uncertainty patterns in LLM response
 - Spawn background research (don't block the reply)
 - Queue: "learn about X"
 - On next turn, incorporate the new knowledge

---

## PART 7: WRITE TESTS FOR 6 MONEY MODULES

Create tests/test_crm.py (10 tests):
 - test_add_client, test_update_stage, test_record_interaction
 - test_lead_scoring_budget, test_lead_scoring_stage
 - test_get_active_leads, test_get_pipeline_summary
 - test_search_clients, test_duplicate_client_update

Create tests/test_invoicing.py (10 tests):
 - test_create_invoice, test_mark_paid, test_list_pending
 - test_invoice_total_calculation, test_crm_sync
 - test_invoice_status_lifecycle, test_overdue_detection
 - test_multiple_invoices_same_client

Create tests/test_payments.py (10 tests):
 - test_process_payment, test_duplicate_rejection
 - test_scan_email_receipts (mock email agent)
 - test_bus_event_emission
 - test_crm_update_on_payment, test_manual_payment_entry

Create tests/test_opportunity_scorer.py (10 tests):
 - test_calculate_win_probability, test_score_opportunity_ev
 - test_record_outcome_weight_adaptation
 - test_rank_opportunities, test_budget_sweet_spot
 - test_skill_match_factor, test_competition_factor

Create tests/test_followup_engine.py (10 tests):
 - test_detect_dormant_lead_24h, test_detect_dormant_lead_72h
 - test_respect_max_followups_per_client
 - test_cadence_timing, test_generate_draft
 - test_cold_lead_after_max_followups

Create tests/test_money_briefing.py (10 tests):
 - test_generate_briefing_structure
 - test_top_actions_populated, test_empty_pipeline
 - test_revenue_calculation, test_briefing_caching

Total: ~60 tests. Run python -m pytest tests/ -q after writing.
All 386 tests must pass (326 original + 60 new).

---

## PART 8: VERIFICATION CHECKLIST

INFRASTRUCTURE:
 [ ] convo.py does NOT block main loop during LLM generation
 [ ] BargeInManager processes frames during LLM generation
 [ ] All 5 money API endpoints return valid JSON
 [ ] TTFT exposed in health endpoint

UNIFIED INTELLIGENCE:
 [ ] money_intelligence.py exists with get_context()
 [ ] Brain injects MoneyIntelligence context (not just raw numbers)
 [ ] knowledge.py exists with KnowledgeSynthesizer
 [ ] Deep research auto-connects new knowledge to existing
 [ ] skills.py exists with SkillExtractor
 [ ] Deep research extracts actionable skills
 [ ] Brain injects relevant knowledge + skills before ALL responses
 [ ] Proactive learning triggers on uncertainty

MONEY:
 [ ] All 6 money modules have tests (60 tests total)
 [ ] All 386 tests pass
 [ ] Money briefing surfaces to user proactively

If ANY checkbox is unchecked, do not report done.

---

## RULES

1. Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
2. Do NOT modify files outside mk2/ and tests/
3. Do NOT break any test
4. Do NOT add external dependencies
5. No bare except: pass in critical paths
6. All money amounts stored as float, formatted $X.XX in speech
7. Every new module must have _log_event() for structured logging
8. If you can't wire something, add TODO and move on
9. Report: changes per part, new files created, test results, unchecked items, final score

## START

cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-9-final

Work order: Part 1 (convo) → Part 2 (API) → Part 3 (money intelligence) → Part 4 (cross-domain) → Part 5 (skills) → Part 6 (proactive) → Part 7 (tests) → Part 8 (verify).
