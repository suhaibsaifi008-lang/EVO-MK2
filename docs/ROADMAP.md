# EVO MK2 — Completion Roadmap
**Goal:** full movie-Jarvis capability set (minus hardware, voice deferred) with zero accumulated bugs.
**Method:** every milestone ships only with tests + a golden scenario + manual QA gate. Nothing merges red.

---

## Capability Coverage Map (Jarvis trait → MK2 milestone)

| # | Jarvis capability | Milestone |
|---|---|---|
| C1 | Total digital context (email/calendar/files) | M2, M6 |
| C2 | Deep research in minutes | M3 |
| C3 | Document/web ingestion + Q&A memory | M3, M4 |
| C4 | Screen & vision awareness | M2 |
| C5 | Files/documents control by voice/text | M2 |
| C6 | Reminders, timers, alarms | M2 |
| C7 | Calendar awareness (today/upcoming) | M2, M5 |
| C8 | Background autonomous missions | M4 |
| C9 | Self-expanding skills | M4 |
| C10 | Watchers (battery/disk/page/news/security) | M5 |
| C11 | Spoken daily briefing | M5 |
| C12 | Notification arbitration (knows when to shut up) | M5 |
| C13 | Communication proxy (mail triage/draft/send-gated) | M6 |
| C14 | Phone presence (Telegram) | M6 |
| C15 | Personality + tone adaptation | M7 |
| C16 | Nightly regression replays (never regress again) | M7 (harness starts M2) |
| — | Duplex voice | PARKED (architecture ready: EVO_WAKE=1) |

---

## M2 — REAL WORK FOUNDATION  *(files · vision · time · calendar-read)*
**New modules:** `mk2/fs_tools.py`, `mk2/vision_tools.py`, `mk2/time_tools.py`, `mk2/calendar_tools.py`, `reminders` table + due-dispatcher in kernel.

Tools added:
- `fs_read(path)` / `fs_write(path, content)` / `fs_search(dir, pattern)` — path allowlist: `~/Documents`, `~/Desktop`, `~/Downloads`, `EVO-MK2/data`. Anything outside → `PermissionDenied`.
- `docs_read(path)` — .txt/.md/.csv native; .pdf via pypdf; .docx via python-docx.
- `screenshot()` — PowerShell capture → `data/screenshots/*.png`; returns path.
- `screen_read(question?)` — screenshot → vision-role model describes/answers (uses router `vision` role).
- `clipboard_get/set` — PowerShell bridge.
- `reminder_add(text, when)/list/cancel(id)` — DB-backed; kernel tick fires due items onto bus → console toast (+TTS later).
- `calendar_today/upcoming` — reads iCal URL (Settings), minimal VEVENT parser, timezone-safe.

Acceptance:
- [ ] Path escape attempt (`..\\..`) → denied + audited
- [ ] "What's on my screen?" returns accurate description of a known window
- [ ] Reminder fires within 5s of due time, exactly once
- [ ] iCal fixture parses 10/10 events with correct times
- [ ] New unit tests ≥ 20; full suite green; 1 golden scenario added

## M3 — RESEARCH & KNOWLEDGE ENGINE
**New:** `research.py` pipeline, `knowledge` table + FTS5 index, `yt_tools.py`.

- `deep_research(topic)` **job**: multi-query search → fetch top pages → extract → synthesize (primary role) → report saved to `vault/reports/<slug>.md` → episode logged. Progress streamed on bus.
- `ingest_url(url)` / `ingest_file(path)` → chunked into knowledge FTS index.
- `recall_knowledge(query)` tool → top passages (FTS5 rank + recency).
- `youtube_summary(url)` — transcript fetch + fast-model summary.
- Long-task UX: live progress chips in console; cancellable.

Acceptance:
- [ ] Mocked end-to-end research produces a cited markdown report
- [ ] Ingested doc answers a factual question via recall
- [ ] YouTube link returns a correct 5-bullet summary (live QA)
- [ ] Jobs survive kernel restart mid-run (resume test)

## M4 — AUTONOMY (missions v2 + skill forge)
**New:** `jobs_runner` subsystem consuming the `jobs` table; `skills/` dir + forge tools.

- Orchestrator gains `task_start(goal)` → creates job, spawns worker thread with restricted toolset `{web_search, web_fetch, fs_write, vault_write, docs_read}` and its own step budget.
- Checkpoint after EVERY step; `task_resume(id)` continues; crash-safe by construction.
- Repeated-intent detector proposes skills: *"You've done X 3 times — make it a skill?"* → `skill_save(name, code)` with test-run before arming (MK1 concept, hardened).
- Skills callable as tools forever after; listed in manifest.

Acceptance:
- [ ] Kill -9 kernel mid-job → restart → job resumes at last checkpoint
- [ ] Skill saved with failing code is rejected, never registered
- [ ] Manifest grows dynamically and orchestrator uses the new skill unprompted

## M5 — PROACTIVE JARVIS
**New:** `watchers.py` engine, `arbitration.py`, `briefing.py`, `weather.py` (open-meteo, keyless).

Watcher kinds: `battery_low`, `disk_high`, `page_changed(url hash)`, `news_keyword(search)`, `calendar_soon(mins)`.
Arbiter rules (hard-coded policy engine): priority tiers, quiet hours, dedup window, max interruptions/hour → survivors become console toasts + spoken line (TTS if enabled).

Daily briefing (08:00 default, configurable): weather + today's calendar + due reminders + overnight watcher hits + open jobs status → composed → saved to vault/journal → spoken/printed.

Acceptance:
- [ ] Battery watcher fires once (not repeatedly) under threshold
- [ ] Page-change detected between two fetched snapshots
- [ ] Quiet-hours suppresses toasts but still queues them
- [ ] Briefing includes ≥4 sections and runs <10s

## M6 — PRESENCE (phone + comms)
**New:** `telegram_link.py` (long-polling, pairing-locked chat id), `mail_tools.py` (IMAP read + SMTP draft-first send), optional ntfy push.

- Telegram: same brain, `surface="telegram"`; /pair flow; deny-by-default.
- Mail: `mail_unread`, `mail_read(n)`, `mail_draft(to,subject,body)` always shows draft; `mail_send` requires BOTH explicit approval and Setup toggle.
- Push: watcher/briefing mirrors to phone via ntfy topic.

Acceptance:
- [ ] Unpaired chat_id gets zero responses
- [ ] Draft never sends without approval flag
- [ ] Telegram turn appears in console history identically

## M7 — PERSONALITY, STYLE, EVAL HARNESS
- `persona.md` loaded at boot (identity, humor range, address rules) — editable by user.
- **Style controller**: post-pass on fast role — classifies user tone (angry/excited/technical/casual) → adjusts verbosity/formality of final answer; strips forbidden patterns.
- **Replay harness** (`evals/replay.py` + `evals/scenarios.jsonl`): every milestone adds ≥1 recorded scenario (input → expected intent/tool/reply-contains). One command runs all against current stack; failures block release.
- Latency SLA report from traces: p50/p95 per stage printed after each replay run.

Acceptance:
- [ ] 60+ scenarios in replay set; suite green
- [ ] Angry-tone input demonstrably yields shorter, calmer reply (mocked eval)
- [ ] No turn in replay exceeds latency SLA without an annotated cause

---

## Anti-Bug Discipline (applies to every milestone)
1. **Test gate:** no feature lands without unit tests covering happy path + failure path + permission denial.
2. **Golden scenario:** each feature adds one replay scenario used forever after.
3. **Hard bounds:** every network/model call wrapped in wall-clock deadline (no silent hangs — ever).
4. **Audit everything:** every state-changing action lands in the ledger.
5. **Trace SLAs:** new stages must log timings; replay fails on SLA breaches.
6. **No regex for fuzzy problems, no LLM for exact problems** — deterministic lane stays deterministic.
7. **One subsystem per commit-series; full suite green before commit.**

## Suggested pace
Fast-but-safe cadence assuming focused sessions: **M2 → M3 → M4 → M5 → M6 → M7**, roughly one to two sessions each. Voice re-entry happens AFTER M7, using the replay harness to validate it like everything else.
