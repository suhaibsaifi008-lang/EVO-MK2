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

Voice, three ways (all in one `python run.py`):

1. **Voice v2 (recommended)** - console topbar **Voice** button or
   <http://127.0.0.1:8421/voice/client/> -> Connect -> just talk.
   WebRTC duplex: Silero VAD endpointing (~0.3-0.5s), local faster-whisper,
   FreeLLMAPI fastest-stable route, Kokoro neural TTS. Barge-in works.
   Every turn lands in MK2 memory + audit via the tools registry.
2. Push-to-talk button in the composer (`/api/transcribe` + `/api/tts`).
3. Legacy wake word: say **"wake up evo"** with `EVO_WAKE=1` (Gemini Live if
   `GEMINI_API_KEY` is set; otherwise fully offline Vosk). "goodbye" ends it.

Standalone dev-runner equivalent: `py -3 voice_bot.py --transport webrtc`
(serves the same pipeline on :7860/client).

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
| `mk2/telegram_link.py` | Phase 2: pairing-locked Telegram bot (same brain, notify mirror) |
| `mk2/mail_tools.py` | Phase 2: IMAP read + draft-first SMTP send (double-gated) |
| `mk2/push_notify.py` | Phase 2: ntfy.sh push + notify.out bridge to the phone |
| `mk2/voice/` | Gateway state machine, STT(+grammar rescue), TTS hybrid, Gemini Live |
| mk2/voice/webrtc_v2.py | Voice v2: WebRTC pipeline embedded in the console server (memory + bus + audited tools) |

## Tests

```powershell
pytest tests -q        # 84 tests, ~9s
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
