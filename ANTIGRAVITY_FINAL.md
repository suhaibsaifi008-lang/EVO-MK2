# ANTIGRAVITY — EVO MK2 Final Push to 9/10
## Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
## Start: git checkout -b jarvis-9-final

You are the final hardening agent. Previous passes built the money modules
and fixed most bugs. This pass fixes the remaining issues AND adds
unified money intelligence.

Score target: 9/10.

---

## PART 1: FIX CONVO.PY BLOCKING (CRITICAL — voice is broken right now)

File: mk2/voice/convo.py, line 137

Current: self.pipeline.process_utterance(text) runs inside the main audio loop.
This blocks the entire loop for 2-5 seconds while the LLM generates.
During this block: mic stops reading, barge-in stops processing, VAD stops running.
The user cannot interrupt. Voice is broken.

Fix: Run process_utterance in a separate thread.

def _run(self):
 stream = stt_mod.Stream()
 audio_q = queue.Queue(maxsize=100)

 def _reader():
 import sounddevice as sd
 try:
 sd.RawInputStream(samplerate=16000, channels=1, dtype='int16',
 blocksize=960, callback=lambda f, t, s, u: audio_q.put_nowait(bytes(f)))
 while not self._stop.is_set():
 time.sleep(0.1)
 except Exception:
 pass

 threading.Thread(target=_reader, daemon=True).start()

 # Pipeline runs in its own thread, main loop only handles mic + barge-in
 reply_queue = queue.Queue()

 def _process(text):
 try:
 result = self.pipeline.process_utterance(text, surface="voice")
 reply_queue.put(result)
 except Exception as exc:
 log.warning("Voice pipeline error: %s", exc)
 reply_queue.put(None)

 processing = False

 while not self._stop.is_set():
 try:
 frame = audio_q.get(timeout=0.5)
 except Exception:
 continue

 lvl = _rms(frame)
 noise_floor = 0.9 * noise_floor + 0.1 * lvl
 speaking = getattr(self.speaker, "is_speaking", False) or self.pipeline.is_active
 self.barge_in_mgr.process_frame(frame, is_playing=speaking)
 self.pipeline.feed_mic_frame(frame)

 kind, text = s.feed(frame)
 if not text:
 continue

 now = time.time()
 quiet = lvl < max(320.0, noise_floor * 2.2)
 if _should_finalize(kind, text, quiet, last_key, last_change, now):
 last_key = ""
 low = text.lower()
 if any(x in low for x in EXIT_PHRASES):
 self.speaker.say("Closing conversation mode.")
 break
 if not processing:
 processing = True
 threading.Thread(target=_process, args=(text,), daemon=True).start()
 last_key = ""
 # Check if pipeline finished
 try:
 result = reply_queue.get_nowait()
 if result:
 pass # pipeline handles its own TTS
 processing = False
 except Exception:
 pass

Key requirements:
- Main loop NEVER blocks on LLM or TTS
- Barge-in processes every frame, even during LLM generation
- Pipeline runs in daemon thread
- Use a queue to signal completion back to main loop
- If pipeline fails, fall back gracefully
- Do NOT break existing behavior

---

## PART 2: ADD 5 MONEY API ENDPOINTS

File: mk2/server.py
Add after existing autonomy endpoints (after /api/autonomy/health):

@app.get("/api/money/briefing")
def get_money_briefing_api():
 try:
 from .money_briefing import get_money_briefing_engine
 briefing = get_money_briefing_engine().generate_briefing()
 return {"ok": True, "briefing": briefing}
 except Exception as exc:
 return {"ok": False, "error": str(exc)}

@app.get("/api/money/pipeline")
def get_money_pipeline_api():
 try:
 from .money_engine import get_money_engine
 engine = get_money_engine()
 return {
 "ok": True,
 "funnel": engine.revenue.get_funnel_metrics(days=30),
 "running": engine.running,
 "last_tick": engine.last_tick_ts,
 }
 except Exception as exc:
 return {"ok": False, "error": str(exc)}

@app.get("/api/money/clients")
def get_money_clients_api():
 try:
 from .crm import get_crm
 crm = get_crm()
 clients = []
 for c in crm.list_clients():
 clients.append({
 "id": c.id, "name": c.name, "email": c.email,
 "platform": c.platform, "stage": c.stage,
 "lead_score": c.lead_score, "total_revenue": c.total_revenue,
 "updated_at": c.updated_at,
 })
 return {"ok": True, "clients": clients}
 except Exception as exc:
 return {"ok": False, "error": str(exc)}

@app.get("/api/money/opportunities")
def get_money_opportunities_api():
 try:
 from .money_engine import get_money_engine
 engine = get_money_engine()
 opportunities = engine.scan_opportunities()
 scored = engine.scorer.rank_opportunities(opportunities) if opportunities else []
 return {
 "ok": True,
 "opportunities": [
 {
 "id": o.id, "title": o.title, "platform": o.platform,
 "budget": o.budget, "win_probability": o.win_probability,
 "expected_value": o.expected_value, "recommendation": o.recommendation,
 }
 for o in scored[:20]
 ],
 }
 except Exception as exc:
 return {"ok": False, "error": str(exc)}

@app.post("/api/money/followup/{client_id}")
def post_money_followup(client_id: str):
 try:
 from .followup_engine import get_followup_engine
 engine = get_followup_engine()
 actions = engine.get_pending_followups()
 target = next((a for a in actions if a.client_id == client_id), None)
 if not target:
 return {"ok": False, "error": "No follow-up needed for this client"}
 # Generate the draft (don't send — user must approve)
 return {"ok": True, "draft": target.draft_message, "cadence": target.cadence}
 except Exception as exc:
 return {"ok": False, "error": str(exc)}

All endpoints: permission="execute", add to existing auth check.

---

## PART 3: UNIFIED MONEY INTELLIGENCE

The brain should UNDERSTAND money, not just call tools. This means the LLM
has money context baked into its reasoning, not fetched per-tool-call.

### 3a. Build Money Intelligence Context (mk2/money_intelligence.py)

Create a module that builds a rich money context string for the LLM:

class MoneyIntelligence:
 def __init__(self):
 self.crm = get_crm()
 self.revenue = get_revenue_tracker()
 self.scorer = get_opportunity_scorer()
 self.briefing = get_money_briefing_engine()

 def get_context(self) -> str:
 Build a COMPLETE money snapshot that the LLM can reason about:
 - 30-day revenue, month-over-month change
 - Pipeline value (sum of all active lead budgets * win probability)
 - Active clients by stage with scores
 - Best performing gig types (from CRM tags + revenue)
 - Response rate, win rate, average pay
 - Top 3 actions for today
 - Upcoming deliverables and payment due dates
 - Opportunity: highest EV gig currently available

 This becomes the LLM's "knowledge" of the business.
 It can answer ANY money question without calling tools.

 def answer(self, question: str) -> str:
 Use the LLM to answer money questions using the context.
 The LLM has all the data — it reasons about it directly.
 No tool calls needed for analysis questions.

### 3b. Wire into brain.py

In mk2/brain.py, in the money keyword detection (around line 525):
Instead of injecting raw numbers, inject the full MoneyIntelligence context.

Current (bad):
 crm_ctx = f"- 30-Day Revenue: ${funnel_stats.get('total_revenue', 0.0):,.2f}..."

New (good):
 from .money_intelligence import get_money_intelligence
 mi = get_money_intelligence()
 money_ctx = mi.get_context()
 system_extra = f"\n=== YOUR BUSINESS ===\n{money_ctx}\n=== END ===\n"
 system_extra += "\nWhen the user asks about money, use this context to reason " \
 "directly. You understand their business — answer conversationally, " \
 "with specific numbers and actionable recommendations. Don't call tools " \
 "for analysis questions — you already have the data."

This makes the LLM genuinely intelligent about money. It can:
 - Say "Your response rate dropped this week — your winning proposals average
 89 words, try shortening them"
 - Say "You have $2,400 in pipeline value with a 35% expected close rate"
 - Say "Follow up with ABC Corp — it's been 5 days and they're your hottest lead"
 - WITHOUT calling any tools. It reasons from context.

### 3c. Keep tools for ACTIONS only

The LLM should still call tools when it needs to:
 - Send a proposal (tool: submit_proposal)
 - Create an invoice (tool: create_invoice)
 - Record a payment (tool: record_payment)
 - Scan Upwork (tool: scan_gigs)

But for ANALYSIS and RECOMMENDATIONS, the LLM uses its unified context.
This is how a real assistant works — it knows your business and advises you,
it doesn't read you numbers from a spreadsheet every time you ask.

---

## PART 4: WRITE TESTS FOR 6 MONEY MODULES

Create tests/test_crm.py:
 - test_add_client, test_update_stage, test_record_interaction
 - test_lead_scoring_budget, test_lead_scoring_stage
 - test_get_active_leads, test_get_pipeline_summary
 - test_search_clients

Create tests/test_invoicing.py:
 - test_create_invoice, test_mark_paid, test_list_pending
 - test_invoice_total_calculation, test_crm_sync

Create tests/test_payments.py:
 - test_process_payment, test_duplicate_rejection
 - test_scan_email_receipts (mock email agent)
 - test_bus_event_emission
 - test_crm_update_on_payment

Create tests/test_opportunity_scorer.py:
 - test_calculate_win_probability
 - test_score_opportunity_ev
 - test_record_outcome_weight_adaptation
 - test_rank_opportunities

Create tests/test_followup_engine.py:
 - test_detect_dormant_lead
 - test_respect_max_followups
 - test_cadence_timing_24h_72h_7d
 - test_generate_draft

Create tests/test_money_briefing.py:
 - test_generate_briefing_structure
 - test_top_actions_populated
 - test_revenue_calculation
 - test_briefing_caching

Total: ~60 tests. Run python -m pytest tests/ -q after writing.

---

## PART 5: VERIFICATION CHECKLIST

INFRASTRUCTURE:
 [ ] convo.py does NOT block the main loop during LLM generation
 [ ] BargeInManager processes frames even while LLM is thinking
 [ ] All 5 money API endpoints exist and return valid JSON
 [ ] TTFT is exposed in health endpoint
 [ ] Voice context trimmed to 5 messages
 [ ] All 386 tests pass (326 original + 60 new)

MONEY INTELLIGENCE:
 [ ] money_intelligence.py exists with get_context() and answer()
 [ ] Brain injects MoneyIntelligence context on money queries
 [ ] LLM can reason about money without calling tools
 [ ] Tools still used for actions (proposals, invoices, payments)
 [ ] Money briefing surfaces to user proactively

If ANY checkbox is unchecked, do not report done.

---

## RULES

1. Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
2. Do NOT modify files outside mk2/ and tests/
3. Do NOT break any test
4. Do NOT add external dependencies
5. No bare except: pass in critical paths
6. Report: PART 1 changes, PART 2 new files, PART 3 wiring, test results, UNCHECKED items, final score

## START

cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-9-final

Work order: Part 1 (convo fix) -> Part 2 (API endpoints) -> Part 3 (unified intelligence) -> Part 4 (tests) -> Part 5 (verify).
