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
| Telegram link | Pairing-locked long-poll bot; same brain/tools; notify mirror |
| Email | IMAP read + draft-first SMTP send (double-gated, single-use drafts) |
| Push (ntfy) | notify.out events pushed to the phone via ntfy.sh |
| Mission runner (Phase 3) | DAG-chained missions w/ checkpoints, resume, strategy rotation, `job.progress` streaming, full-surface fan-out |
| Deep memory (Phase 4) | Embedded episodic recall + knowledge-graph triples injected into every turn |
| RAG (Phase 4) | Ingest any folder of docs → cited Q&A over them |
| Deep Thought (Phase 4) | Analyst/skeptic/advisor ensemble merged into one superior answer |
| Whisper PTT (voice) | faster-whisper small.en local transcription, Vosk fallback, 48k→16k resample |
| Token cascade (router) | Ranked model ladder per role; quota-exhausted models cooldown and EVO slides to the next |
| Skills forge v2 (Phase 5) | AST security audit + test-run gate; learned skills re-arm on boot |
| Workflows (Phase 5) | YAML chains w/ daily/interval schedules, sequential audited execution |
| Habits (Phase 5) | 3-repeat detection → proposal → approval-gated automation |
| Connectors (Phase 5) | Declarative JSON spec → live REST tool instantly, persisted across restarts |
| Docs delivery (Phase 5) | docs_create/docs_append: markdown → real .docx, agent EXECUTION RULE (deliver, never outline) |
| DevAgent (Phase 5.5) | code_read/search/edit/write/test + devtask loop; ALL writes approval-gated; selfcheck auto-diagnosis w/ revert-on-red safety net |
| Security (Phase 6) | url_check gate on every opened link, breach_check, security_scan posture report |
| Life admin (Phase 6) | Bank-CSV expense ledger, monthly category summaries, recurring-charge subscription audit |
| Secrets vault (Phase 6) | DPAPI-encrypted store; values masked in speech/audit; connectors draw tokens straight from it |
| Persona (Phase 7) | Editable persona.md IS the identity - injected every turn, applies instantly |
| Style controller (Phase 7) | Tone classification (angry/stressed/joking/terse...) reshapes each reply + feedback loop |
| Initiative engine (Phase 7) | EVO speaks first from a curiosity queue; quiet hours + daily cap + presence checks |

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
- [x] Telegram message triggers real action on PC (same tools as console)
- [x] Email draft shown but never sent without explicit approval flag AND Setup toggle
- [x] Unpaired Telegram chat_id gets zero responses
- [ ] Push notification arrives on phone within 5s of trigger *(code live; pending on-device check once NTFY_TOPIC is set)*

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
- [x] Kill kernel mid-mission → restart → mission resumes from last checkpoint
- [x] Mission completes → result delivered to console AND Telegram AND push
- [x] Tool failure 3x → tries alternative approach before giving up *(strategy rotation blocks a tool after 2 consecutive failures and forces a different route)*
- [x] Mission with dependency waits for prerequisite to complete first

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
- [x] "What did we discuss about scholarships three weeks ago?" returns accurate recall *(auto-summarizer embeds episodes every 10 min; search_episodes = semantic)*
- [x] Ingested document answers factual questions correctly via RAG *(txt/md/csv/json/pdf/docx; pdf needs optional pypdf)*
- [ ] deep_thought produces measurably better answers on hard problems (evaluated by rubric) *(ensemble built + merge-tested; formal rubric eval pending)*
- [x] Vector search returns semantically related (not just keyword-matched) episodes *(Gemini embedding-001 when key set; offline hash fallback)*

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
- [x] Skill saved with failing code is rejected and NOT registered
- [x] Skill saved with working code runs correctly on next invocation
- [x] Workflow executes 3 skills sequentially without intervention
- [x] New REST API connector works within one conversation turn
- [x] AST security audit blocks subprocess/socket/eval/exec in self-written skills
- [x] Habit detection proposes automation after 3 repeats; approval required (proposals table dedupes)

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
- [x] Known-malicious URL flagged before browser opens *(open_app gates every URL through url_check)*
- [x] Breach check returns accurate results for test email *(XposedOrNot free by default; HIBP if HIBP_API_KEY set; email masked in audit ledger)*
- [x] Subscription audit identifies at least one recurring charge *(flexible bank-CSV ingest, merchant normalization, ±15% amount matching)*
- [x] Secrets stored encrypted, never appear in logs or audit trail *(Windows DPAPI bound to login; args masked incl. emails)*

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
- [ ] After 30 days, vault + persona file accurately represent the user *(all mechanisms live: persona.md, feedback loop, compression - matures with use)*
- [x] Angry input produces shorter, calmer reply (style controller working)
- [x] EVO initiates conversation about something interesting without being asked *(quiet hours, daily cap, conversation-presence checks)*
- [x] No "As an AI" disclaimers, no forced "sir", no robotic phrasing in any reply *(persona hard rules injected every turn)*

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
