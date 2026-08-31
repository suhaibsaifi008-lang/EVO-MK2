# ANTIGRAVITY — EVO MK2 Money Jarvis Final Push to 9/10
## Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
## Start: git checkout -b jarvis-money-specialist

You are the final agent. Previous passes fixed bugs and built features.
This pass wires the orphaned features and builds the money specialization layer.

Score target: 9/10 as a personal assistant that makes money autonomously.

---

## PART 1: FIX REMAINING INFRASTRUCTURE GAPS

### 1a. Wire OverlappedVoicePipeline into active voice path
File: mk2/voice/streaming.py exists but NOTHING imports it. It's dead code.
The active voice paths are:
 - mk2/voice/gateway.py (v1, EVO_WAKE=1)
 - mk2/voice/live.py (Gemini duplex)
 - mk2/voice/convo.py (conversation mode)
 - mk2/voice/webrtc_v2.py (Pipecat WebRTC)

Pick ONE path to wire streaming into. The best candidate is convo.py
(ConversationMode) — it already has barge-in, it has a clean audio loop,
and it's the most actively used voice surface.

In mk2/voice/convo.py, in the ConversationMode._run() loop where it
calls self.speaker.say(text):
 - Before speaking, check if OverlappedVoicePipeline is available
 - If yes, use pipeline.process_utterance(text) instead of speaker.say()
 - The pipeline handles sentence-chunked TTS with barge-in automatically
 - If pipeline fails, fall back to speaker.say() (existing behavior)

Do NOT break existing behavior. If streaming import fails, use the old path.

### 1b. Add BargeInManager to webrtc_v2
File: mk2/voice/webrtc_v2.py
Currently relies on pipecat's allow_interruptions=True (line 286).
No TURN server exists, so pipecat interruptions are unreliable.

Add after the pipeline setup (around line 286):
 - Import BargeInManager from .barge_in
 - Create instance with callback that calls the pipeline's interrupt method
 - If the pipeline exposes an audio transport/callback, feed frames through BargeInManager
 - If no audio transport is accessible, add a comment explaining the limitation
 - Do NOT break the existing pipecat pipeline

### 1c. Prioritize fast model for voice in LLM router
File: mk2/llm.py, function _attempts() around line 227
Currently: all roles use the same ladder order. Voice gets primary (slow) models first.

Fix: when role == "voice" or role == "fast", reorder attempts so fast models
come first. Specifically:
 - After building the attempts list, if role in ("voice", "fast"):
 - Sort attempts so entries using fast_model come first
 - Use _ttft (measured TTFT) to reorder within the fast group
 - This makes the fast model the FIRST attempt, not the last
 - Do NOT change behavior for role == "primary" or role == "reasoning"

### 1d. Reduce STT silence finalize threshold
File: mk2/voice/convo.py, line 38
Current: _should_finalize requires 0.4s of stable partial
Change to: 0.3s (300ms instead of 400ms)
This is the silence-before-finalize threshold. Lower = faster response.

File: mk2/voice/stt.py
Check if there is a silence/finalize threshold parameter.
If found, reduce it to 300ms. If not found, add a comment explaining
that finalize is handled upstream in convo.py/gateway.py.

### 1e. Trim voice context window
File: mk2/llm.py, function chat_stream() or wherever voice context is built
Currently: voice calls send full message history.
Fix: when role == "voice", send only:
 - 
 - last 2 user/assistant message pairs (4 messages max)
 - current user message
Total: 5 messages max for voice. This reduces token processing time.

For role == "primary" and role == "reasoning", keep existing behavior.

### 1f. Add TTFT tracking to health endpoint
File: mk2/server.py, /api/autonomy/health endpoint
Add to the response:
 - "llm_ttft_ms": average TTFT for fast model in last hour
 - Read from llm.py's _ttft dict if accessible
 - If not accessible, return null
 - This lets the user see real latency numbers in the health check

---

## PART 2: MONEY SPECIALIZATION — THE CORE DIFFERENTIATOR

This is what makes EVO MK2 different from a generic assistant.
It must be specialized for ONE thing: making money autonomously.

### 2a. Client CRM (mk2/crm.py) — NEW FILE

Build a Client Relationship Management system that tracks every
client interaction from first contact to payout.

Schema (SQLite table: clients):
 - id, name, email, platform (upwork/fiverr/gumroad/stripe/email)
 - first_contact_ts, last_contact_ts
 - total_earned, total_billed, total_paid
 - status: lead / contacted / proposed / hired / delivered / paid / repeat
 - notes (free text from interactions)
 - tags (comma-separated: "web-scraping", "automation", "urgent", etc.)

Schema (SQLite table: interactions):
 - id, client_id, ts, type (proposal/response/delivery/payment/checkin)
 - direction (inbound/outbound)
 - subject, body_summary
 - outcome (sent/read/responded/accepted/rejected)

Methods:
 - add_client(name, email, platform, notes="") -> client_id
 - record_interaction(client_id, type, direction, subject, body, outcome)
 - get_client(client_id) -> client dict with interaction history
 - get_active_leads() -> clients with status in [lead, contacted, proposed]
 - get_awaiting_delivery() -> clients with status=hired, no delivery recorded
 - get_awaiting_payment() -> clients with status=delivered, no payment
 - get_client_score(client_id) -> float 0-100 based on:
 * communication responsiveness (time to reply)
 * budget alignment (their budget vs your rate)
 * platform reputation (Upwork rating, etc.)
 * payment history (paid on time?)
 - get_daily_briefing() -> dict with:
 * clients_to_followup (last contact > 3 days ago, status not closed)
 * payments_expected (delivered but not paid)
 * proposals_pending (sent but no response)
 * warmest_leads (highest score among active leads)
 - search_clients(query) -> fuzzy match on name, email, notes, tags

### 2b. Invoice & Proposal Generator (mk2/invoicing.py) — NEW FILE

Auto-generates professional invoices and proposals from templates.

Invoice:
 - Input: client_id, hours_worked, rate, description
 - Output: formatted Markdown invoice + PDF-ready HTML
 - Auto-includes: client info, line items, total, payment terms
 - Saves to vault as "invoices/{client_name}/{date}_invoice.md"
 - Records payment record in revenue tracker

Proposal:
 - Input: gig_data (from Upwork/Fiverr scrape), client_id (optional)
 - Output: tailored proposal using:
 * Client's specific pain points from gig description
 * Your past relevant work from CRM
 * Rate based on client's budget and your tier
 - Max 4 sentences, no fluff, no "Dear Hiring Manager"
 - Includes: what you'll do, why you're the right fit, price, timeline
 - Saves to vault as "proposals/{client_name}/{date}_proposal.md"

### 2c. Payment Detection Pipeline (mk2/payments.py) — NEW FILE

Automatically detects when money arrives through multiple channels.

Channels:
 1. Upwork messages: Scan Upwork inbox for "hired", "contract sent", "payment"
 2. Email scan: Search inbox for payment confirmations (Stripe, PayPal, bank)
 3. Stripe webhooks: If Stripe configured, listen for charge.succeeded
 4. Manual: User says "I got paid $X from Y"

Detection flow:
 - On each brain tick (every 30 min), run payment_scan()
 - Check Upwork messages for new contract/payment indicators
 - Check email for payment keywords + amounts
 - If payment detected:
 * Record in RevenueTracker.record_payment()
 * Update CRM client status to "paid"
 * Record interaction in CRM
 * Trigger daily briefing update
 * If amount > $500, send celebration notification

### 2d. Daily Money Briefing (mk2/money_briefing.py) — NEW FILE

The #1 feature the user sees every morning. Must be actionable.

Briefing format:
 ```
 GOOD MORNING. HERE'S YOUR MONEY BRIEFING.

 CASHFLOW:
 - Earned this week: $X (Y payments)
 - Earned this month: $X
 - Pipeline value: $X (Z active proposals)

 TODAY'S PRIORITIES:
 1. Follow up with [CLIENT] — last contact 5 days ago, proposal pending
 2. Deliver [PROJECT] for [CLIENT] — due today
 3. Check Upwork — 3 new matching gigs posted overnight

 PIPELINE:
 - 5 active leads (2 hot, 3 warm)
 - 2 proposals awaiting response (>3 days)
 - 1 delivery awaiting payment ($350)

 RECOMMENDATION:
 [AI-generated: e.g., "Your response rate is 40%. Try shortening proposals
 to 3 sentences — your winning proposals average 89 words vs 134 for
 non-winning ones."]

 WIN RATE: X% (Y wins / Z proposals)
 AVG PAY: $X/hr | BEST DAY: $X on [date]
 ```

Methods:
 - generate_briefing() -> formatted string
 - Pulls from: RevenueTracker, CRM, MoneyEngine funnel, Upwork scan
 - Caches for 4 hours (don't regenerate every minute)
 - Triggered: on user request ("money briefing"), daily at 8am, after any payment event

### 2e. Opportunity Auto-Scorer (mk2/opportunity_scorer.py) — NEW FILE

Scores every incoming gig/opportunity with a win probability.

Features:
 - Takes gig data (title, description, budget, client info)
 - Scores based on:
 * Keyword match with your past winning proposals (from CRM + revenue DB)
 * Budget vs your typical rate
 * Client platform + rating (Upwork 5-star > new client)
 * Competition indicators (number of proposals on gig)
 * Timing (gigs posted <1 hour ago score higher)
 - Returns: score (0-100), reasoning, suggested_bid, suggested_timeline
 - Learns from outcomes: if you win a scored gig, reinforce the scoring weights
 - If you lose, adjust weights

Integration:
 - Called by money_engine.scan_opportunities() before evaluation
 - Called by Upwork agent before proposal submission
 - Scores stored in CRM for trend analysis

### 2f. Autonomous Client Follow-Up Engine (mk2/followup_engine.py) — NEW FILE

Automatically maintains client relationships without being annoying.

Rules:
 - Lead (no contact): send introduction within 24h of first scrape
 - Contacted (no response after 3 days): polite follow-up
 - Proposed (no response after 2 days): brief follow-up
 - Hired (no delivery in agreed timeline): status check
 - Delivered (no payment after 7 days): payment reminder
 - Paid (no contact after 14 days): check-in for repeat work

Each follow-up:
 - Generated by LLM using CRM context (past interactions, client's style)
 - Tone matches previous conversation
 - Max 3 follow-ups per client (then mark as "cold")
 - All follow-ups logged in CRM interactions
 - User can override/skip any follow-up

### 2g. Money Dashboard API (mk2/server.py)

Add endpoints:

GET /api/money/briefing
 Returns today's money briefing (same format as 2d)

GET /api/money/pipeline
 Returns funnel metrics + active leads + pending proposals + expected payments

GET /api/money/clients
 Returns client list with scores, statuses, last contact dates

GET /api/money/opportunities
 Returns scored opportunities from recent scans

POST /api/money/followup/{client_id}
 Triggers a follow-up for a specific client

All endpoints require permission="execute" or higher.

---

## PART 3: WIRE EVERYTHING INTO THE MONEY LOOP

### 3a. Update JarvisAgent daily tick
File: mk2/jarvis_agent.py (find the daily tick / scheduled tasks method)
Add to the daily morning routine:
 1. Run money_briefing.generate_briefing()
 2. Publish as proactive notification via bus.publish("notify.out", ...)
 3. If any payments_expected are overdue, escalate to "urgent" in briefing
 4. If any hot leads need followup, include in briefing action items

### 3b. Update MoneyEngine to use CRM + scorer
File: mk2/money_engine.py
 - On scan_opportunities(), run each opportunity through opportunity_scorer first
 - Sort by score descending before pick_best_opportunity()
 - On execute_opportunity(), after proposal_submit:
 * Create CRM client entry if new
 * Record interaction in CRM
 - Add method: daily_money_tick() that:
 * Scans Upwork for new gigs
 * Runs payment detection
 * Checks for follow-ups needed
 * Returns summary dict

### 3c. Connect UpworkAgent to CRM
File: mk2/platforms/upwork.py
 - In submit_proposal(): after submission, check if client exists in CRM
 * If not: create client entry with status="proposed"
 * Record interaction: type=proposal, direction=outbound
 - In check_proposal_status(): if status changed, update CRM client status
 - In check_messages(): if client responded, record as inbound interaction
 - In scrape_gigs(): for each gig, extract client_name and create/update CRM lead

### 3d. Wire payment detection into brain tick
File: mk2/jarvis_agent.py or wherever the brain tick runs
Add to the periodic check (every 30 min):
 - Call payments.payment_scan()
 - If new payment detected:
 * Play notification sound
 * Publish bus event "money.payment_received"
 * Add to daily briefing

---

## PART 4: MONEY JARVIS PERSONA

### 4a. Update for money context
The JARVIS brain should understand money context. When the user mentions
money-related topics, the brain should:
 - Reference CRM data (past clients, rates, win rate)
 - Suggest actions (follow up with X, send invoice for Y)
 - Use financial intelligence (response rates, best days, etc.)

File: mk2/brain.py or mk2/persona_loader.py
 - Add money-specific context injection gigs
 * "how much", "what's my", "show me money", "briefing"
 - Inject: current week earnings, pipeline value, active leads count
 - Do NOT break existing persona behavior for non-money topics

---

## PART 5: VERIFICATION CHECKLIST

Before reporting done, verify ALL of these:

INFRASTRUCTURE:
 [ ] OverlappedVoicePipeline is imported and used by at least ONE voice surface
 [ ] BargeInManager exists in webrtc_v2.py (even if limited)
 [ ] Fast model is tried before primary model when role=voice
 [ ] STT finalize threshold is <= 300ms in at least one active voice path
 [ ] Voice context is trimmed to 5 messages max
 [ ] Health endpoint reports TTFT data
 [ ] All 326 tests still pass

MONEY SPECIALIZATION:
 [ ] crm.py exists with Client + Interaction tracking
 [ ] invoicing.py exists with invoice + proposal generation
 [ ] payments.py exists with multi-channel payment detection
 [ ] money_briefing.py exists with actionable daily briefing format
 [ ] opportunity_scorer.py exists with win probability scoring
 [ ] followup_engine.py exists with automated client follow-ups
 [ ] /api/money/* endpoints exist and return data
 [ ] MoneyEngine uses CRM and scorer in scan_opportunities()
 [ ] UpworkAgent creates CRM entries on proposal submission
 [ ] JarvisAgent daily tick includes money briefing
 [ ] Brain has money context injection

If ANY checkbox is unchecked, it's not done. Do not report success
with incomplete work.

---

## RULES

1. Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
2. Do NOT modify files outside mk2/ and root config
3. Do NOT break any test. Run python -m pytest tests/ -q after Part 1.
4. Do NOT add external dependencies without checking requirements.txt
5. No bare except: pass in mk2/crm.py, mk2/invoicing.py, mk2/payments.py, mk2/money_briefing.py, mk2/opportunity_scorer.py, mk2/followup_engine.py
6. Every new module must have a _log_event() helper for structured logging
7. All money amounts must be stored as float, formatted as $X.XX in speech output
8. If you can't wire something, add a TODO comment and move on
9. Report: PART 1 changes, PART 2-4 new files created, test results, UNCHECKED items, and final score

## START

cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-money-specialist

Work order: Part 1 (infrastructure) -> Part 2 (money modules) -> Part 3 (wiring) -> Part 4 (persona) -> Part 5 (verify checklist).
