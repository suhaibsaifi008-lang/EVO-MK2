# EVO MK2 — The 7 Phases to Real Jarvis

> **Goal:** transform EVO from a chatbot into a proactive, autonomous, self-expanding personal intelligence — movie-Jarvis minus the hardware.
>
> **Voice/speech/mic: PARKED.** Architecture is ready (`EVO_WAKE=1`), will be revisited after Phase 7.

---

## What's Already Built (v0.2)

| Capability | How it works |
|---|---|
| Instant commands (<250ms) | Fast-lane: open/close apps/sites, volume, time, screenshots |
| Streaming chat via FreeLLMAPI | 203 models, auto-routing, failover across providers |
| Screen vision | Screenshot → Gemini vision → describe/answer |
| Deep research | Multi-source search → synthesis → cited report |
| Reminders with dispatcher | Due-dispatcher fires exactly once, delivered via SSE |
| Markdown vault | Human-readable memory files you can edit yourself |
| Semantic facts | Key-value upserts injected into every conversation |
| File system control | Read/write/search inside allowed folders only |
| Document reading | PDF, DOCX, CSV, MD, JSON |
| Shell commands | Sandboxed PowerShell with audit trail |
| Audit ledger | Every tool call logged immutably |
| Self-diagnosis | `/api/diag` reports every subsystem's health in one call |
| Living orb face | Canvas animation reacting to listen/think/speak states |

---

## PHASE 1 — AWARENESS
*"Jarvis always knows."*

EVO stops being reactive and becomes aware of your world.

### Build
- `watchers.py` — background polling engine for battery, disk, processes, web pages
- `arbiter.py` — priority tiers + quiet hours + dedup + max-interruptions-per-hour
- `briefing.py` — composes daily summary from weather + calendar + tasks + watcher outputs
- `context_tracker.py` — tracks focused app, clipboard content patterns

### Tools Added
- `watcher_add(kind, target, threshold)` / `watcher_list` / `watcher_remove(id)`
- `briefing_now`

### Acceptance Criteria
- [ ] Battery warning fires ONCE at threshold, not repeatedly
- [ ] Daily briefing appears at configured time with weather + schedule + alerts
- [ ] Page-change detected between two fetched snapshots of same URL
- [ ] Quiet hours suppress toasts but queue them for later delivery
- [ ] Max interruptions per hour enforced (no spam)

---

## PHASE 2 — COMMUNICATION
*"Jarvis handles all of Mr. Stark's correspondence."*

EVO reaches beyond your machine.

### Build
- `telegram_link.py` — long-polling bot, pairing-locked chat IDs, same brain/tools/memory as console
- `mail_tools.py` — IMAP read + SMTP send (draft-first, double-gated)
- `push_notify.py` — ntfy.sh topic sends alerts to your phone

### Tools Added
- `mail_unread` / `mail_read(n)` / `mail_draft(to,subject,body)` / `mail_send_approved`
- Telegram is a surface, not a tool — same brain responds

### Acceptance Criteria
- [ ] Telegram message triggers real action on PC (same tools as console)
- [ ] Email draft shown but never sent without explicit approval flag AND Setup toggle
- [ ] Unpaired Telegram chat_id gets zero responses
- [ ] Push notification arrives on phone within 5s of trigger

---

## PHASE 3 — AUTONOMY
*"Jarvis, handle it."* — and Jarvis handles it. For hours. Without supervision.

### Build
- Upgrade `jobs.py`: LLM-driven step planning (not just tool calls), strategy rotation on failures
- Task dependencies: DAG-based chaining ("after researching X, write report")
- Progress streaming to ALL surfaces simultaneously
- Boot-time resume for interrupted missions
- Result delivery fan-out (console + TTS if enabled + Telegram + push)

### Tools Added
- `task_start(goal)` / `task_status(id)` / `task_stop(id)` / `task_resume(id)`

### Acceptance Criteria
- [ ] Kill kernel mid-mission → restart → mission resumes from last checkpoint
- [ ] Mission completes → result delivered to console AND Telegram AND push
- [ ] Tool failure 3x → tries alternative approach before giving up
- [ ] Mission with dependency waits for prerequisite to complete first

---

## PHASE 4 — DEEP INTELLIGENCE
*"Jarvis, I need to know everything about this."*

EVO gains real memory and multi-model reasoning.

### Build
- `deep_memory.py` — sqlite-vec vector store + local embedding model for episodic memory search
- `rag.py` — document ingestion pipeline: chunk → embed → index → retrieve-augmented generation
- `ensemble.py` — deep thought: parallel reasoning passes merged into superior answers
- Knowledge graph table: subject-predicate-object triples

### Tools Added
- `remember_episode(text, importance)` / `search_episodes(query)`
- `ingest_documents(folder)` / `ask_documents(question)`
- `deep_thought(question)` — three specialist passes merged

### Acceptance Criteria
- [ ] "What did we discuss about scholarships three weeks ago?" returns accurate recall
- [ ] Ingested PDF answers factual questions correctly via RAG
- [ ] deep_thought produces measurably better answers on hard problems (evaluated by rubric)
- [ ] Vector search returns semantically related (not just keyword-matched) episodes

---

## PHASE 5 — SELF-EXPANSION
*"Jarvis builds his own tools."*

### Build
- Upgrade skills forge: AST-validate → sandbox test-run → register permanently
- Workflow chains: YAML-defined sequences of skills run on schedule or trigger
- Habit detection: track repeated intents → offer automation
- API connector framework: declarative JSON specs turn any REST API into a tool

### Tools Added
- `workflow_create(yaml_def)` / `workflow_run(name)` / `workflow_list`
- `connector_add(api_spec_json)` — instant new REST API tool
- Habit proposals appear automatically after 3 repetitions

### Acceptance Criteria
- [ ] Skill saved with failing code is rejected and NOT registered
- [ ] Skill saved with working code runs correctly on next invocation
- [ ] Workflow executes 3 skills sequentially without intervention
- [ ] New REST API connector works within one conversation turn

---

## PHASE 6 — SECURITY & LIFE ADMIN
*"Jarvis protects Mr. Stark."*

### Build
- `security.py` — system monitoring, phishing detection, breach checks
- `life_admin.py` — expense categorization, subscription audit
- `vault_secrets.py` — Windows Credential Manager integration for API keys

### Tools Added
- `security_scan` / `breach_check(email)` / `url_check(url)`
- `expense_summary(month)` / `subscription_audit`
- `secret_store(key, value)` / `secret_get(key)`

### Acceptance Criteria
- [ ] Known-malicious URL flagged before browser opens
- [ ] Breach check returns accurate results for test email
- [ ] Subscription audit identifies at least one recurring charge
- [ ] Secrets stored encrypted, never appear in logs or audit trail

---

## PHASE 7 — THE RELATIONSHIP
*"Jarvis isn't software. He's a presence."*

### Build
- `persona_loader.py` — loads editable `persona.md` (identity, values, humor range)
- `style_controller.py` — classifies user tone → adjusts response verbosity/formality/temperature
- `initiative_engine.py` — EVO starts conversations based on watcher outputs + curiosity model
- Opinion formation: tracks which answer styles get positive vs negative follow-up
- Conversation compression: month-long chats stay coherent through importance-scored summarization

### Tools Added
- `set_persona(file_path)` / `get_persona_summary`

### Acceptance Criteria
- [ ] After 30 days, vault + persona file accurately represent the user
- [ ] Angry input produces shorter, calmer reply (style controller working)
- [ ] EVO initiates conversation about something interesting without being asked
- [ ] No "As an AI" disclaimers, no forced "sir", no robotic phrasing in any reply

---

## Dependency Graph

```
Phase 1 (Awareness) ──────────┐
                               ├──→ Phase 3 (Autonomy) ──→ Phase 5 (Self-expansion)
Phase 2 (Communication) ──────┤
                               ├──→ Phase 6 (Security)
Phase 4 (Deep Intelligence) ───┤
                               └──→ Phase 7 (Relationship)
```

Phases 1+2 can run in PARALLEL. Phase 3 needs both. Phase 4 is independent.
Phases 5-7 build on everything before them.

---

## Anti-Bug Discipline (every phase, no exceptions)

1. **Test gate:** happy path + failure path + permission denial tested
2. **Golden scenario:** each feature adds one replay scenario used forever
3. **Hard bounds:** every network/model call has wall-clock deadline
4. **Audit everything:** state-changing actions logged immutably
5. **Trace SLAs:** stage timings logged; replay fails on SLA breaches
6. **No regex for fuzzy, no LLM for exact**
7. **One subsystem per commit-series; full suite green before commit**

## What Each Phase FEELS Like

| Phase | The moment you notice |
|---|---|
| **1** | "It told me my battery was low BEFORE I asked" |
| **2** | "I texted it from my car and it opened YouTube" |
| **3** | "I asked it to compare 5 laptops and got a report 20 minutes later" |
| **4** | "It remembered a conversation we had THREE WEEKS AGO" |
| **5** | "It WROTE ITS OWN TOOL to solve a problem I didn't explain fully" |
| **6** | "It blocked a phishing email before I saw it" |
| **7** | "It disagreed with me and was right" |

## Voice (PARKED)

Architecture ready: Gemini Live duplex bridge coded, Vosk wake word + grammar rescue built,
PTT button wired. Re-enable with `EVO_WAKE=1`. Will be revisited after Phase 7 using the
replay harness to validate quality like every other subsystem.
