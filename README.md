# EVO MK2 — Personal Intelligence, rebuilt properly

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

Voice: say **"wake up evo"** → duplex session opens (Gemini Live if `GEMINI_API_KEY`
is set; otherwise fully offline Vosk). Interrupt it anytime. "goodbye" ends the session.

## Layout

| Path | Role |
|---|---|
| `mk2/kernel.py` | Loop owner + subsystem supervisor (auto-restart w/ backoff) |
| `mk2/bus.py` | Event bus — no-replay contract, thread-safe publish |
| `mk2/db.py` | SQLite: messages, facts, episodes, traces, audit, jobs |
| `mk2/llm.py` | Model router: roles primary/fast/reasoning + failover + streaming |
| `mk2/tools/` | Structured-result tools (`{ok,speech,data}`), permissions, audit |
| `mk2/brain.py` | Orchestrator loop (fast-path → tool steps → streamed answer) |
| `mk2/memory.py` | Context builder + reflection-pass write policy |
| `mk2/server.py` | FastAPI: SSE chat stream, health, audit/memory views |
| `mk2/voice/` | Gateway state machine, STT(+grammar rescue), TTS hybrid, Gemini Live |

## Tests

```powershell
pytest tests -q        # 35 tests, ~2s
```

## Milestones

- [x] M0 skeleton — supervisor / bus / db / traces / health
- [x] M1 brain — router + orchestrator + SSE console
- [x] Voice v1 — wake sessions, grammar-rescued commands, Live/local engines
- [ ] M2 deeper tools (files, calendar, mail) + job workers
- [ ] M4 vector episodic recall · replay eval harness
