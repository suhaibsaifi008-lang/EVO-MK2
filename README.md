# EVO MK2 â€” Personal Intelligence, rebuilt properly

Second-generation rebuild of EVO following the [Jarvis blueprint](docs/ARCHITECTURE.md):
supervised processes, an event bus with a no-replay contract, a role-based model
router, a permissioned tool registry with an immutable audit ledger, tiered memory,
streaming SSE conversation, and a voice gateway (Gemini Live duplex + fully-offline
Vosk fallback).

## Run

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # add your keys
python run.py            # console at http://127.0.0.1:8421
```

Voice: say **"wake up evo"** â†’ duplex session opens (Gemini Live if `GEMINI_API_KEY`
is set; otherwise fully offline Vosk). Interrupt it anytime. "goodbye" ends the session.

## Layout

| Path | Role |
|---|---|
| `mk2/kernel.py` | Loop owner + subsystem supervisor (auto-restart w/ backoff) |
| `mk2/bus.py` | Event bus â€” no-replay contract, thread-safe publish |
| `mk2/db.py` | SQLite: messages, facts, episodes, traces, audit, jobs |
| `mk2/llm.py` | Model router: roles primary/fast/reasoning + failover + streaming |
| `mk2/tools/` | Structured-result tools (`{ok,speech,data}`), permissions, audit |
| `mk2/brain.py` | Orchestrator loop (fast-path â†’ tool steps â†’ streamed answer) |
| `mk2/memory.py` | Context builder + reflection-pass write policy |
| `mk2/server.py` | FastAPI: SSE chat stream, health, audit/memory views |
| `mk2/voice/` | Gateway state machine, STT(+grammar rescue), TTS hybrid, Gemini Live |

## Tests

```powershell
pytest tests -q        # 35 tests, ~2s
```

## Milestones

Full completion roadmap with acceptance gates: [docs/ROADMAP.md](docs/ROADMAP.md)

- [x] M0 skeleton — supervisor / bus / db / traces / health
- [x] M1 brain — router + orchestrator + SSE console
- [x] Voice v1 — PTT transcribe/tts endpoints, Gemini Live bridge (parked)
- [ ] M2 real-work foundation — files, vision, reminders, calendar-read
- [ ] M3 research engine — deep research, ingestion, YouTube, FTS recall
- [ ] M4 autonomy — job runner w/ checkpoints, skill forge
- [ ] M5 proactive — watchers, arbitration, spoken daily briefing
- [ ] M6 presence — Telegram, draft-first email, push
- [ ] M7 personality/style controller + nightly replay harness
