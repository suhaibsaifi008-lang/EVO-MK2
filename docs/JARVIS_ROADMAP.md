# Project J.A.R.V.I.S. â€” Roadmap
### Turning NousResearch/hermes-agent into a real-life, software-only Jarvis

> Foundation: **fork of `NousResearch/hermes-agent` (MIT)**.
> Donor organ: **EVO MK2** (this repo) â€” mined for proven ideas, then retired to R&D status.
> Scope: software only. No microphones/speakers/robots â€” voice happens through existing devices
> (PC mic, phone, Discord VC, Home Assistant media players).

---

## Guiding principles

1. **Adopt before you build.** Every phase starts by configuring stock Hermes. Only code when a gap survives contact with reality.
2. **Extend through the seams** (skills, MCP servers, plugins, hooks, SOUL.md, cron) â€” these survive upstream merges. Hard-core edits are quarantined in Phase 10.
3. **Stay mergeable.** Any fork commit must be rebaseable onto upstream `main` within one hour. If it isn't, it belongs in a plugin instead.
4. **Latency is a feature.** Every phase ends with a measured number (wakeâ†’first-audio seconds, etc.), committed alongside the code â€” same discipline as MK2.
5. **Sir stays in charge.** Proactivity is rate-limited, quiet-hours aware, and every destructive action is approval-gated. A Jarvis that spams or acts without consent gets muted forever.

## Target shape (end state)

```
                         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
   "Hey Jarvisâ€¦" â”€â”€â”€â”€â”€â”€â–º â”‚  Voice layer                 â”‚
   phone / PC / HA       â”‚  wake word â†’ STT â†’ LLM â†’ TTS â”‚
                         â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                        â”‚
        Telegram / Discord / WhatsApp â–º â”‚ â—„ Desktop app / CLI / IDE (ACP)
                                        â”‚
                         â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                         â”‚  HERMES CORE (fork)          â”‚
                         â”‚  agent loop Â· memory loop Â·  â”‚
                         â”‚  skills Â· cron Â· gateway     â”‚
                         â””â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”˜
                            â”‚       â”‚       â”‚       â”‚
                      MCP: Windows  MCP:    plugins: email,
                      controlÂ·files life-data HAÂ·ntfyÂ·webhooks
                            â”‚       â”‚       â”‚       â”‚
                       your PC   calendar/mail  house (SW side)
```

---

## Phase 0 â€” Foundation (weekend)

**Goal:** vanilla Hermes talking, on your machine, on free models.

- [ ] Install Hermes on native Windows (supported) under a dedicated profile: `hermes -p jarvis`
- [ ] Provider plan (no paid subscription required to start):
  - Primary brain: OpenRouter free tier (e.g. a strong free model) **or** LM Studio/Ollama via OpenAI-compatible endpoint for 100% local
  - Fast lane: Groq free tier (also doubles as cloud STT later)
  - Fallback chain configured so quota-death never silences Jarvis (MK2 lesson)
- [ ] Run `hermes setup`, `hermes model`, `hermes tools`; skim `hermes doctor`
- [ ] Decide hosting posture: always-on desktop vs $5 VPS + phone access (gateway makes either work)
- [ ] Set up git fork + branch `jarvis/main` tracking upstream `main`

**Acceptance:** typed question answered < ~3 s to first token, from both PC and (after Phase 3) phone; fork pushes clean.

## Phase 1 â€” Soul & identity (weekend)

**Goal:** it stops being "an agent" and becomes *him*.

- [x] Write global `SOUL.md`: name (JARVIS), diction (dry British wit, "sir" address, never gushing), brevity law (spoken answers â‰¤ 2 sentences unless asked), humor calibration, refusal-of-sycophancy rule
- [x] Seed `USER.md` with the real dossier: name, timezone, projects, machines, preferences, pet peeves (port the useful half of MK2's persona block)
- [ ] Context file per recurring domain (work, coding, home) so the right knowledge loads by location
- [ ] Personality regression test: a fixed list of 10 prompts whose answers must stay in-character (manual checklist, rerun after every upgrade)

**Acceptance:** the 10-prompt suite passes identically in CLI and (later) Telegram; you catch yourself saying "thanks, Jarvis" unprompted.

## Phase 2 â€” Voice core (1â€“2 weeks)

**Goal:** hands-free, low-latency, interruptible conversation on the PC.

- [x] Install `[voice]` extras + ffmpeg/portaudio
- [x] STT: local `faster-whisper` (free, private) with Groq Whisper as fast fallback
- [ ] TTS: **Piper provider** (MK2 already proved a local Piper JARVIS ONNX voice works great on CPU) â€” premium option later: ElevenLabs/OpenAI; default free: Edge TTS
- [x] Tune `config.yaml`: `silence_threshold`, `silence_duration` (drop below 3.0 s toward ~1.5 s once reliable), `stop_phrases` (`["stop","goodbye jarvis"]`)
- [x] Enable and calibrate **barge-in** (`voice.barge_in: true`, threshold multiplier, grace seconds) â€” talk over mid-answer, verify cut + continuation
- [x] **Wake word**: train/register "hey jarvis" (on-device, v0.20+); false-wake budget: < 1/day
- [x] Whisper hallucination filter verified against silence/noise (MK2 hit this too)
- [ ] Measure and record: wakeâ†’first-audio steady-state target **< 2.5 s** on fast lane

**Acceptance:** 20-turn hands-free convo including two deliberate interruptions, zero stuck states, latency logged in commit message.

## Phase 3 â€” Presence everywhere (1 week)

**Goal:** Jarvis reaches you wherever you are; phone becomes the remote.

- [ ] `hermes gateway` + **Telegram** bot with pairing-lock (port MK2's 6-digit pairing philosophy; strangers get silence)
- [ ] Optional second surface: Discord DM (free voice bubbles + VC participation later)
- [ ] Voice replies on Telegram (`/voice tts`) â€” voice memo in, voice out
- [ ] **ntfy.sh** channel for high-priority push (reminders, job completions) â€” mirrors MK2 `notify.out`
- [ ] Cross-session continuity check: start task on PC, finish from phone
- [ ] Autostart: gateway as an NSSM/Task Scheduler service; survives reboot

**Acceptance:** phone-only day: all interaction through Telegram + ntfy works, incl. one voice exchange and one notification-driven action.

## Phase 4 â€” Memory: he knows you (1â€“2 weeks)

**Goal:** cross-session recall that compounds instead of resetting.

- [ ] Enable memory nudges + curate first `MEMORY.md` entries manually; learn the memory slash-commands
- [ ] **Data migration from MK2**: export `facts`, `episodes`, journal notes from MK2 SQLite â†’ seed Hermes memory files / session history (one-off importer script)
- [ ] Document RAG as an MCP server or skill: point at your folders (txt/md/pdf/docx), chunk + retrieve with citations â€” direct port of MK2 `rag.py` semantics
- [ ] Standing-corrections pattern: `rule:`-style facts honored verbatim (MK2 trick) encoded in MEMORY.md conventions
- [ ] Sensitive-data guardrail convention: passwords/keys/OTP never stored in memory files (regex-checked at import time in the migration script)

**Acceptance:** "what did I tell you about X three weeks ago?" answered correctly; document Q&A cites the source file; nothing secret in any memory file (audit pass).

## Phase 5 â€” Hands on the machine: Windows control (1â€“2 weeks)

**Goal:** Jarvis operates your PC like Tony's workshop.

- [ ] Build **`mcp-jarvis-windows`** MCP server (Python) exposing MK2-proven primitives as tools:
  - app launch/close, process list/kill, volume/mute, media keys
  - screenshot capture (+vision describe via Hermes vision tool)
  - clipboard read/write, open URL behind the security gate (MK2 `security.gate` logic: allowlist + vetting)
  - power actions (lock/sleep â€” never restart/shutdown without explicit confirm)
- [ ] File operations: scoped-root jail (MK2 `fs_tools.ALLOWED_ROOTS` approach) exposed as MCP tools; traversal-proof resolve checks
- [ ] Approval policy: map every new tool to Hermes' dangerous-command/approval tiers; default-deny list documented
- [ ] Secrets stay out of band: DPAPI vault (MK2) remains the store; MCP tools fetch at call time, values never enter transcripts
- [ ] Optional: computer-use tool for the rare GUI-only task

**Acceptance:** by voice alone: "open spotify and set volume 30", "screenshot and read me the error", "lock the pc"; every action lands in the audit view.

## Phase 6 â€” Digital life integrations (2 weeks, parallelizable)

**Goal:** the assistant part of assistant.

- [ ] **Email**: bundled email plugin, IMAP read + *draft-first* send (MK2 double-gate philosophy preserved: draft persisted, explicit "send it" required)
- [ ] **Calendar**: read/write (Google or MS); morning-agenda data source
- [ ] **Reminders**: natural-language reminder â†’ cron job â†’ ntfy/Telegram delivery (replaces MK2 reminders.py entirely)
- [ ] **Research stack**: web_search + web_extract + browser automation; v0.20 grounded research with citations for anything factual
- [ ] **Daily digest inputs**: news feeds, YouTube transcript ingestion (MK2 youtube_tools pattern) as a scheduled job
- [ ] **Home Assistant** (software face of the house): lights/media/scenes via the HA platform plugin
- [ ] **Spotify** control if you use music

**Acceptance gates:** inbox triage by voice ("summarize unread, draft replies to these two"); "what's my day look like" composes calendar+weather+news in one spoken briefing; one HA scene fired by voice.

## Phase 7 â€” Proactivity: he speaks first (1â€“2 weeks)

**Goal:** initiative without annoyance â€” the hardest Jarvis property to tune.

- [ ] **Morning briefing cron**: calendar + email count + weather + top headlines, composed overnight, delivered by chosen channel at chosen hour
- [ ] **Nightly cron**: audit-review + memory-hygiene report ("I noticed X, filed it under Y")
- [ ] Port MK2 **initiative_engine** rules as gateway hooks/cron wrapper: quiet hours (23:00â€“08:00), max 3 unsolicited/day, â‰¥45 min gap, suppressed for 10 min after any conversation
- [ ] Port MK2 **habits miner** as a plugin: watches session/tool history for â‰¥3 repeats of the same tool+target â†’ proposes a cron/workflow ("you've opened X daily â€” automate it?")
- [ ] Signed outbound webhooks (v0.20) for events you want pushed to other systems

**Acceptance:** 7-day soak: â‰¥ 5 genuinely useful unsolicited interventions, â‰¤ 1 regrettable interruption total, zero during quiet hours.

## Phase 8 â€” Autonomy & self-improvement (ongoing)

**Goal:** he gets better while you sleep.

- [ ] Turn on autonomous **skill creation**; review the first 10 auto-created skills, prune junk, promote keepers
- [ ] Curate a personal **Skills Hub** set; write 5 hand-crafted skills for your real routines (standups, paper hunts, invoice folder, game-server careâ€¦)
- [ ] **Subagent delegation** for parallel research; `execute_code` pipelines for multi-step data chores
- [ ] **Bot mode**: specialist bots (e.g. `@researcher`, `@sysadmin`) in a group chat with you + JARVIS orchestrating
- [ ] Scheduled self-checks replacing MK2 `selfcheck.py` (disk, endpoints, model reachability)

**Acceptance:** a novel 3-step task done twice gets autonomously captured as a skill and reused correctly the third time, hands-off.

## Phase 9 â€” Hardening & always-on life (1 week + continuous)

**Goal:** appliance-grade reliability and privacy.

- [ ] Full-offline fallback drill: kill internet â†’ Ollama local brain + whisper-local STT + piper TTS keep him alive (degraded-but-alive contract)
- [ ] Security pass: DM pairing on every platform, allowlists tight, command-approval level reviewed per surface, `.env` permissions, no secrets in logs (grep ritual)
- [ ] Backup: `~/.hermes` profile (memory, skills, sessions, config) versioned/restorable; tested restore
- [ ] Update ritual: monthly `git fetch upstream` + rebase + run the Phase-1 personality suite + smoke tests before deploying
- [ ] Observability: log rotation, `hermes doctor` in a weekly cron, token-spend report if using paid APIs

**Acceptance:** unplugged-router test passes; simulated disk-loss restore completes < 30 min with zero memory loss.

## Phase 10 â€” Fork differentiators (only when Phases 0â€“9 feel limiting)

The MK2 DNA worth merging into core â€” attempt **upstream PRs first**, carry as local patches second:

- [ ] **TTFT race-streaming router**: top-2 routes race, first token wins, loser abandoned; stall-benching + EWMA reordering (MK2 `llm.py` â€” the single best idea in this repo)
- [ ] **Fast-lane pre-processor**: deterministic command regexes (time/date/simple controls) resolved with zero model calls
- [ ] **Initiative engine as first-class subsystem** (not cron-wrapper) with the quiet-hours/cap/gap governor
- [ ] **SurfaceÃ—permission enforcement matrix**: single choke-point check binding channels to allowed tool permissions (fixes MK2's own declared-but-unenforced gap)
- [ ] Workflow schedule-state persistence (MK2 lost it on reboot â€” don't repeat)

**Rule:** each item lands as â‰¤ 3 small PRs or dies. Divergence budget: fork may not exceed ~2k changed LOC vs upstream.

---

## Model & cost strategy

| Layer | Free path | Paid upgrade |
|---|---|---|
| Brain | OpenRouter free models / Ollama-LM Studio local | OpenRouter paid / Claude / GPT when a task deserves it |
| Fast lane | Groq free tier | Groq paid |
| STT | faster-whisper local | Groq Whisper (~0.5 s) |
| TTS | Piper (JARVIS voice, local CPU) | ElevenLabs |
| Push | ntfy.sh | â€” |

Target steady state: **$0/month functional**, paid tiers optional quality boosts.

## Risk register

| Risk | Mitigation |
|---|---|
| Upstream moves fast; fork rots | Seams-not-core doctrine; monthly rebase ritual; divergence budget (Ph. 10) |
| Proactivity annoys â†’ gets disabled forever | Governor defaults conservative; one-strike rollback command |
| Voice latency regresses after upgrades | Measured-number-per-change discipline (commit logs) |
| Memory drifts into wrong/secret territory | Curation ritual + sensitive-regex import guard + audit pass |
| Over-engineering before daily use | Each phase gated on *using* the previous one for a week |

## "Feels like Jarvis" final checklist

- [ ] Wake word â†’ natural sentence answer, interruptible anytime, < 2.5 s
- [ ] Same soul on PC, phone, and voice
- [ ] Knows your calendar, mail, documents, habits, and corrections without being told twice
- [ ] Operates the PC and the house by voice, audited and approval-gated
- [ ] Briefs you in the morning; flags what matters; silent otherwise
- [ ] Captures its own skills; visibly better each month
- [ ] Survives reboot, outage, and your own forgetfulness
