# EVO MK2 — Post-Mortem Diagnostic Report
## "The Most Realistic JARVIS" — Why It Doesn't Work in Reality

*Prepared: 2026-08-29 | Scope: Full codebase audit*

---

## Executive Summary

EVO MK2 is a massively ambitious personal-assistant project with ~25+ subsystems spanning voice, LLM routing, browser automation, desktop control, memory, autonomy, initiative, goal management, document RAG, security vetting, and more. The architecture shows clear intelligence and genuine engineering skill — but as a **working system**, it's fundamentally broken in multiple compounding ways. The roadmap promises a JARVIS experience. The reality is a fragile Rube Goldberg machine where almost every subsystem has at least one silent failure mode, and the failure modes stack.

The core problem: **the project confuses having a feature in code with having a working feature**. There is no integration testing, no end-to-end smoke test, no canary that tells you "yes, the voice pipeline works from mic to speaker right now." Each subsystem was built and tested in isolation, then assumed to compose.

---

## Section 1: The Model Layer Is Built on Fantasy Hardware

### 1.1 Non-existent model ladder

`mk2/llm.py` lines 23-35 define the primary model ladder:

```python
PRIMARY_LADDER = [
 "gpt-oss-120b", # rank 1 - score 0.927
 "command-a-vision", # rank 2 - 0.886, vision
 "gpt-5.4-mini", # rank 3 - intelligence 100
 "inkling", # rank 4 - intelligence 99
 "nemotron-3-ultra-550b", # rank 12 - intelligence 99-100, 1M ctx
 "", # rank 6 - speed 99 workhorse
]
MODEL_LADDERS = {
 "fast": ["", "gpt-oss-20b", "gpt-5.4-nano"],
 "reasoning": ["gpt-5.4-mini", "inkling", "nemotron-3-ultra-550b",
 "nemotron-3-ultra", "o3"],
}
```

**Problem:** None of these models exist on any public API. `gpt-oss-120b`, `command-a-vision`, `gpt-5.4-mini`, `inkling`, `` — these are not OpenAI, Anthropic, Google, Groq, or model identifiers. The code will attempt to call them, get 404/model-not-found errors, penalize them, and cascade to the next entry. If *every* model in the ladder is fictional, the system hits every cooldown, then falls through to the `llm.chat()` oneshot fallback, which hits the same dead ladders.

The `"anthropic"` provider is referenced in `_attempts()` (line 175) and `_providers()` never actually creates an Anthropic provider entry — there's no `anthropic_key` field in Settings. So the "anthropic" name in the attempts list is a phantom that can never resolve.

**Impact:** The primary brain is dead by default. The "fast lane" model `` is also non-existent. The `gpt-5.4-mini` and `o3` are also not standard identifiers. If the user's `JARVIS_MODEL_FAST` and `JARVIS_OPENAI_MODEL` env vars aren't set to working models, the entire LLM layer is non-functional.

### 1.2 `_hard_bounded` can't actually kill runaway LLM calls

```python
def _hard_bounded(fn, seconds):
 box = {}
 def runner():
 try:
 box["r"] = fn()
 except BaseException as exc:
 box["e"] = exc
 th = threading.Thread(target=runner, daemon=True)
 th.start()
 th.join(max(1.0, float(seconds)))
 if "r" not in box:
 raise LLMUnavailable(f"timed out after {seconds:.0f}s")
 return box["r"]
```

**Problem:** Python threads cannot be killed. If the underlying `urllib.request.urlopen()` blocks in the OS kernel (e.g., TCP handshake timeout, DNS resolution), `th.join()` returns but the thread lives forever, holding the socket. Each timeout leaks a daemon thread. Under load, this consumes file descriptors and memory. The `_race_stream` function starts *two* of these threads per voice turn — if both stall, you leak threads at 2x.

### 1.3 The race stream races wrong routes

`_race_stream()` fires two providers with different *models* from the same provider. The whole point of racing is to send the same request to two different *providers* so whichever responds first wins. But the code selects `pairs = [(p, m) for p, m in attempts if p.get("kind") == "openai"][:2]` — both pairs use the same provider (`freellmapi`), just different models on it. If the provider is down, both racers die simultaneously and you get `info["no_token"] = True`.

---

## Section 2: The Voice Pipeline Is a Frankenstein

### 2.1 Three competing voice systems, none fully working

The codebase has **three separate voice subsystems** that partially overlap:

| System | File | Status |
|--------|------|--------|
| Voice Gateway (v1) | `mk2/voice/gateway.py` | Started via `_voice_subsystem()` in kernel, but **only if `EVO_WAKE=1`** |
| WebRTC Voice (v2) | `mk2/voice/webrtc_v2.py` | Registered on FastAPI app at import time |
| Pipecat Voice | `mk2/voice/webrtc_v2.py` (pipeline) | Built inside v2, uses `pipecat-ai` |
| Voice Convo | `mk2/voice/convo.py` | Toggled via `/api/voice/convo` endpoint |

The kernel only starts the v1 gateway when `EVO_WAKE=1`. The v2 WebRTC system registers routes on the FastAPI app but its actual pipeline only runs when a browser client connects to `/voice/client`. The `/ws/voice` WebSocket in `server.py` (line 140) does its *own* separate voice synthesis path using `tts_best` (Piper/Kokoro). These three paths have **completely different code paths, different TTS engines, and different LLM routing** — none of which share state.

### 2.2 Voice bypasses the brain entirely

The v2 voice pipeline (`run_pipeline()` in `webrtc_v2.py` line 237) creates its **own** `OpenAILLMService` from pipecat, pointing at `settings.openai_base` with `settings.openai_model` (the PRIMARY, slowest model). It does **not** use `mk2.llm.chat_stream()`. This means:
- Voice responses skip the LLM failover ladder entirely
- Voice uses the primary model (slowest) instead of the fast model
- The stall-recovery, racing, TTFT tracking, and provider switching in `llm.py` are **all bypassed**
- The persona injection, tool manifest, and memory context from `brain.handle_turn()` are **not used** — voice has its own simplified 

### 2.3 WebRTC requires a STUN/TURN server that may not work

The ICE configuration (line 369 of `webrtc_v2.py`) only offers a Google STUN server:
```python
{"iceServers": [{"urls": ["stun:stun.l.google.com:19302"}]}
```

No TURN server is configured. On networks where STUN fails (symmetric NATs, corporate firewalls, many Indian ISP networks), WebRTC connection establishment fails silently. The client sees a blank page, and the only error is a debug print to stderr.

### 2.4 `publish_threadsafe` doesn't exist — crashes awareness silently

`mk2/awareness.py` lines 173-175:
```python
from .bus import publish_threadsafe
publish_threadsafe(topic, payload)
```

The `Bus` class in `mk2/bus.py` has `publish()` and `subscribe_async()` but **no function called `publish_threadsafe`**. Every time awareness tries to publish an alert, it gets an `ImportError`, which is caught by the bare `except Exception` in the calling loop. The user never gets battery alerts, disk alerts, or page-change alerts — they just silently never fire.

This same broken import exists in `mk2/tools/life_tools.py` line 219.

---

## Section 3: The Brain Is Fragile Under Real Conversations

### 3.1 Context window grows unbounded

`build_context_messages()` in `memory.py` loads **every** message from `db.recent_messages(14)` (line 96) at full 1200 chars each. It also loads all facts, vault notes, rules, episodes, triples, and knowledge-graph matches. For a long conversation, this easily exceeds 8K-16K tokens before the user's current message is even added. On models with small contexts or per-request pricing, this is a problem. More critically, the `db.recent_messages(14)` returns 14 *turns* (not 14 messages), but each turn is stored as two entries (user + assistant), so you're loading 28 message objects.

### 3.2 The tool-call parsing is brittle

`parse_tool_call()` (brain.py line 59) scans character-by-character for the first `{` and tries `json.JSONDecoder.raw_decode()`. This means:
- Any `{` in the LLM's prose (e.g., "the options are {red, blue, green}") triggers a parse attempt
- If the LLM writes `{"say": "hello"}` inside a longer response, it gets extracted as a tool call even when the user just wanted a normal reply
- The `sanitize_final()` function tries to strip these but operates on the raw text, not the structured intent

### 3.3 No timeout on tool execution

`tools.call()` in `tools/__init__.py` calls `t.fn(**args)` with no timeout whatsoever. A tool that hangs (e.g., `browser_navigate` to a site that never resolves, `screenshot` on a locked desktop) blocks the entire brain thread. Since `handle_turn()` runs in a thread pool executor, this blocks one worker — but under concurrent requests, all workers get consumed.

### 3.4 The "fast path" regex is naive

`fastlane.py` pattern-matches user input against ~20 regex patterns. the compound splitter at line 21 tries to split on `and`/`then`/`;`, gets `["open chrome", "play despacito on youtube"]`, matches both as action patterns, and runs them. But `open chrome` calls `tools.call("open_app", {"target": "chrome"})` which tries to find Chrome by name — if the app isn't registered in Windows, it silently fails. And the youtube search URL regex at line 107 doesn't handle queries with apostrophes, special characters, or non-English text.

---

## Section 4: The "Autonomy" Features Are Theater

### 4.1 `_execute_browser_task` signature mismatch

In `autonomy.py` line 635:
```python
def _execute_browser_task(self, mission, subtask, exec_prompt):
```

But it's called at line 597:
```python
if tool_hint in ("browser_navigate", "browser_click", "browser_type"):
 return self._execute_browser_task(mission, subtask)
```

**Missing argument:** `exec_prompt` is never passed. This will raise `TypeError: _execute_browser_task() missing 1 required positional argument: 'exec_prompt'` whenever any browser-based subtask runs. The `except Exception` at line 610 catches it, logs "Browser task failed", and returns False. **Every single browser autonomy task silently fails.**

### 4.2 `_execute_subtask` calls `_execute_browser_task` before permission check

Line 597-601:
```python
if tool_hint in ("browser_navigate", "browser_click", "browser_type"):
 return self._execute_browser_task(mission, subtask)

if not is_allowed(tool_hint):
 return self._execute_generic_task(mission, subtask)
```

Browser tasks bypass the permission system entirely. If `EVO_AUTONOMY_LEVEL=safe`, the deny-list includes browser tools — but browser tasks skip the `is_allowed` check and execute anyway.

### 4.3 Goal engine has a double-negative bug

`goal_engine.py` line 127:
```python
if not goal.get("status") != "failed":
 return False
```

This parses as `if goal.get("status") == "failed": return False`. The intent is "retry only if status IS failed" — but the variable is called `retry_goal` and the docstring says "Retry a failed goal." The code *does* return False when the goal is NOT failed, and True when it IS failed. This is accidentally correct! But the double negative makes it unmaintainable, and any future developer will almost certainly "fix" it into a bug.

### 4.4 `_load_auto_goals` silently returns `None` from the JSON parse

Line 802-807:
```python
def _load_auto_goals(self):
 try:
 p = AUTO_DATA / "auto_goals.json"
 if p.exists():
 return json.loads(p.read_text())
 except Exception:
 pass
 return []
```

If `auto_goals.json` exists but is empty or corrupted, `json.loads()` raises, the exception is swallowed, and `None` is returned. Then `_check_proactive_actions()` iterates `None` — `TypeError: 'NoneType' object is not iterable` — caught by the outer `except Exception: pass`. The autonomy loop silently does nothing forever.

### 4.5 The autonomy "continuous loop" has no real goals

`ContinuousAutonomyLoop._check_proactive_actions()` reads `auto_goals.json` and only processes goals with `active=True` and `last_run=0`. After the first run, `last_run` is set to `time.time()`. The loop checks every 30 minutes but only executes **one** goal per tick (line 793: `break` after the first). If `interval_hours=24`, the goal won't be picked up again for 24 hours. But `_load_auto_goals` never filters by `interval_hours`, so the timing logic is aspirational only.

---

## Section 5: Infrastructure and Deployment Are Unsolved

### 5.1 No `setup.py`, `pyproject.toml`, or lockfile

The project has a `requirements.txt` but no package definition, no `setup.py`, no `pyproject.toml`, and no lockfile. This means:
- `pip install -e .` doesn't work
- Dependency versions float freely
- The critical `pipecat-ai[webrtc,silero,whisper,kokoro,openai,runner]>=1.7` has no upper bound — a major version bump of pipecat could break everything
- `kokoro-onnx>=0.4` and `faster-whisper>=1.0` are unpinned and known to have breaking API changes between minor versions

### 5.2 `pip install -r requirements.txt` likely fails

Several packages in requirements.txt require system-level dependencies:
- `sounddevice` needs PortAudio (not installed by pip)
- `faster-whisper` needs a C++ compiler (MSVC on Windows)
- `playwright` needs `playwright install chromium` after pip install
- `pipecat-ai[webrtc]` pulls in `aiortc` which needs libav on some platforms
- `kokoro-onnx` needs ONNX runtime libraries

There's no setup script or Dockerfile to automate this.

### 5.3 Secrets in `.env` with no `.gitignore` evidence

The `config.py` loader reads `ROOT / ".env"` directly. There's no `.gitignore` shown in the repo listing. If `.env` (containing API keys, Telegram tokens, mail passwords) is ever committed, every secret is exposed. The `vault_secrets.py` DPAPI implementation encrypts secrets at rest, but the `.env` file is **never migrated into the vault** — it sits as plaintext alongside the code.

### 5.4 `_no_crypt` fallback stores secrets as plaintext

```python
_no_crypt = (_sys.platform != "win32")
```

On Linux/macOS, `_no_crypt` is True, and `_protect()`/`_unprotect()` return the plaintext unchanged. But more critically, even on Windows, if the DPAPI import fails (line 19-21), `_no_crypt` flips to True, and the entire encryption layer is silently disabled. There's no warning, no log, no indication that secrets are now stored in cleartext in `secrets.bin`.

---

## Section 6: The Security Model Is Mostly Window-Dressing

### 6.1 Permission tiers are declared but never enforced

The `autonomy.py` defines `_PERMISSION_TIERS` with allow/deny lists. But the main `tools/__init__.py` `call()` function (line 84) checks the tool's `permission` field against... nothing. The `PermissionDenied` exception is defined but **never raised** by `call()`. Every tool in the registry is registered with `permission="read"` or `permission="execute"` strings, but `call()` never checks the caller's identity or the requested action against these strings.

The autonomy engine has its own `is_allowed()` check, but the main brain loop (`handle_turn()`) has **no permission enforcement at all**. If the LLM decides to call `shell_run` or `mouse_click`, it just executes.

### 6.2 The browser tool is gated by a URL check that's easily bypassed

`security.py`'s `url_check()` checks heuristics and Google Safe Browsing. But `tools.call("open_app", {"target": "chrome"})` in `fastlane.py` line 128 calls `tools.call("open_app", ...)` which uses `webbrowser.open()` — this goes through the OS, not through the security gate. The security gate only applies if `open_app` explicitly calls `security.gate()`, which many tool implementations don't.

### 6.3 The "surfaces × permissions" matrix from the roadmap was never built

The roadmap (Phase 10) explicitly identifies this: "Surface×permission enforcement matrix: single choke-point check binding channels to allowed tool permissions (fixes MK2's own declared-but-unenforced gap)." This was identified as needed, listed in the roadmap, and **never implemented**.

---

## Section 7: What Actually Works vs. What's Promised

| Feature | Roadmap Claim | Actual State |
|---------|--------------|--------------|
| Wake word "Hey JARVIS" | Phase 2, checked | Requires `EVO_WAKE=1` + v1 gateway, separate from v2 |
| Voice conversation | Phase 2, checked | Three competing systems, none tested end-to-end |
| LLM brain | Phase 0, checked | Model ladder references non-existent models |
| Fast lane | Phase 10, "best idea in repo" | Only ~20 regex patterns; LLM fallback still needed for most queries |
| Memory recall | Phase 4, checked | Context window grows unbounded; no summarization of old facts |
| Document Q&A | Phase 4, checked | Works but loads ALL chunks into context (`db.all_chunks()` in `ask_documents`) |
| Autonomy/goal execution | Phase 8, checked | Browser tasks crash; generic tasks are shallow wrappers |
| Initiative engine | Phase 7, checked | Works but candidates are sparse; depends on reminders/proposals that may not exist |
| Self-healing | Phase 5.5, checked | Runs tests that don't exist; spawns dev tasks that can't execute |
| Desktop control | Phase 5, checked | Tools exist but permission enforcement is missing |
| Telegram/Discord | Phase 3, partially checked | Telegram bridge exists but paired/unpaired state unclear |
| Security vetting | Phase 6, checked | URL checks exist but are not enforced at the tool-call level |
| Browser automation | Phase 5/8, checked | Playwright works but the autonomy layer crashes before reaching it |
| Cross-session continuity | Phase 3, not checked | Memory persists but context loading has no budget |

---

## Section 8: Root Causes — Why It's "Works on Paper"

### 8.1 No end-to-end test

The test directory has 20+ test files, but they are all **unit tests** that test individual functions in isolation. There is no test that says:
1. User speaks a wake word
2. Speech is transcribed
3. LLM processes it
4. A tool is called
5. The tool result is processed
6. A TTS response is synthesized
7. Audio plays back to the user

Without this smoke test, every "fix" to one subsystem breaks another silently, and nobody notices until they try to use it.

### 8.2 Exception swallowing everywhere

The codebase has hundreds of bare `except Exception: pass` blocks. The kernel's `_supervise()` pattern catches all subsystem errors and restarts them — but it **logs at WARNING level and continues**. A subsystem that dies every 30 seconds gets restarted every 30 seconds, generating a flood of log messages that look like "normal operation." Critical errors blend into the noise.

### 8.3 No observability

There is no metrics, no tracing, no structured logging beyond Python's stdlib `logging`. When a voice turn takes 45 seconds, there's no way to tell if the delay is in STT, LLM, or TTS without instrumenting the code manually. The `db.trace()` function records timing but only for the brain's own steps — not for the voice pipeline.

### 8.4 Over-engineering before daily use

The roadmap's own risk register says: "Over-engineering before daily use — Each phase gated on *using* the previous one for a week." But the code has implemented Phase 8 (autonomy) before Phase 3 (cross-session voice via Telegram) is verified working. The project has built an 8th-floor penthouse on a foundation that was never inspected.

### 8.5 Multiple authors, no architectural review

The git history shows commits from different sessions with different focus areas. The `_patch*.py`, `fix_*.py`, `step234.py`, `write_*.py`, `check_*.py` files littering the root are scratch scripts from prior debugging sessions that were never cleaned up. The codebase has clearly had many hands in it without a single unifying review pass.

---

## Section 9: The Critical Path to "It Actually Works"

If the goal is a JARVIS that works reliably, here's the minimum viable path:

1. **Fix the model ladder.** Replace fictional models with real ones (e.g., `gpt-4o-mini`, `claude-sonnet-4-20250514`, `gemini-2.0-flash`). Add a `models.yaml` file that's checked at startup — if no valid provider is reachable, the system should refuse to start with a clear error, not limp along with dead routes.

2. **Unify the voice pipeline.** Pick ONE voice path: either the WebRTC/pipecat path OR the gateway/pycatt path. Remove the other. Ensure it uses the same LLM router as the text chat.

3. **Add a 30-second smoke test** that runs on startup: mic check → STT → LLM ping → TTS → speaker check. If any step fails, report it clearly.

4. **Fix the `publish_threadsafe` bug** in `awareness.py` and `life_tools.py` — either add the function to `bus.py` or replace with `bus.publish()`.

5. **Fix the autonomy browser crash** — add the missing `exec_prompt` parameter to `_execute_browser_task`.

6. **Actually enforce permissions** — add a permission check in `tools.call()` that validates the caller's surface/channel against the tool's required permission level.

7. **Add a `pyproject.toml`** with pinned dependencies and a `postinstall` script that sets up PortAudio, Playwright Chromium, and Piper voices.

8. **Clean up the scratch files** — the 30+ `_patch*.py`, `fix_*.py`, `step*.py`, `write_*.py`, `check_*.py` files in the root and `mk2/` directory should be deleted. They serve no purpose and make the project look unstable.

9. **Implement the "surface × permission" matrix** from the roadmap's Phase 10. Right now, any surface can call any tool.

10. **Add structured logging with levels.** Every subsystem should emit `{subsystem, event, duration_ms, success}` tuples. Without this, diagnosing "why didn't JARVIS respond" is a 2-hour debugging session.

---

## Section 10: Bottom Line

EVO MK2 is not a broken JARVIS. It's a **research prototype** with the ambition of a production system. The engineering is genuinely impressive in scope — an LLM router with failover, a multi-modal voice pipeline, browser automation, desktop control, document RAG, an initiative engine, goal management, and autonomous execution are all real hard problems. Most projects never get close to this breadth.

But the project has never been run end-to-end by anyone who wasn't also writing the code. Every subsystem has at least one silent failure mode. The model ladder points to models that don't exist. The voice pipeline has three overlapping implementations. The autonomy engine crashes on browser tasks. The permission system is decorative. The awareness system imports functions that don't exist.

**The #1 reason it "works on paper but not in reality": there is no integration test that exercises the full user journey from "wake word" to "spoken reply."** Everything was built and verified in isolation. The seams between subsystems were assumed to hold. They don't.

A realistic next step would be: delete everything that isn't proven to work end-to-end, build one working voice loop (wake → STT → LLM → TTS → speaker) with real models, verify it works 10 times in a row, then add one feature at a time with an integration test for each.
