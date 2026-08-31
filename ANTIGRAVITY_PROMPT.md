# ANTIGRAVITY — EVO MK2 Final Push to 9/10
## Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
## Start: git checkout -b jarvis-to-9

You are the final hardening agent. The previous hardening pass fixed the obvious bugs.
This pass fixes the subtle bugs, adds the missing features, and hits performance targets.

---

## PHASE 1: FIX REMAINING BUGS (from independent audit)

### 1a. Remove `api_`/`skill_` backdoor in is_allowed()
File: mk2/autonomy.py, line 128
Remove: `or tool_name.startswith("api_") or tool_name.startswith("skill_")`
That line lets ANY tool name starting with those prefixes bypass the allow-list.
No tools use these prefixes. It's a pure security hole. Delete the two conditions.

### 1b. Kill switch silent failure
File: mk2/kill_switch.py, line 35
Change: `except Exception: pass` → `except Exception as exc: log.warning("KillSwitch: failed to cancel task %s: %s", name, exc)`

### 1c. Health endpoint reports fake data
File: mk2/server.py, around line 884 (/api/autonomy/health)
The endpoint hardcodes crashes_last_hour=0, silent_failures=0, awareness.alive=True, brain.alive=True.
Replace hardcoded values with actual checks:
- crashes_last_hour: count kernel task restarts from kernel._restarts dict (or track with a rolling window)
- silent_failures: count from audit log entries with "permission denied" or error patterns in last hour
- awareness.alive: actually import and check awareness module, not hardcode True
- brain.alive: check if brain thread/process is running, not hardcode True

### 1d. Funnel metrics double-count
File: mk2/revenue.py, lines 132-134
Change `if st == "paid" or act == "payment_received":` to `elif`.
A single record should never count in both conditions.

### 1e. Upwork submit_proposal actually submits
File: mk2/platforms/upwork.py, lines 288-304
Current: fills textarea via JS, never clicks submit.
Add: after filling the form, click the submit button. Try selectors:
- `button:has-text("Submit")`
- `button[type="submit"]`
- `input[type="submit"]`
- `.submit-btn`
- `button[aria-label*="submit" i]`
Wrap in try/except with log.warning on failure. If no selector matches, log that manual submission is needed.

### 1f. Brain silent validation failure
File: mk2/brain.py, line 750
Change: `except Exception: pass` → `except Exception as exc: log.warning("Response validation failed: %s", exc)`

---

## PHASE 2: BARGE-IN (make it actually work)

### 2a. Current state analysis
The barge-in code exists in 3 places:
- mk2/voice/gateway.py line 222: RMS-based VAD during TTS playback
- mk2/voice/live.py line 69: Speaker.interrupt() method
- mk2/voice/tts_stream.py line 85: StreamingTTS.interrupt() method
- mk2/conversation.py: Text-based interruption ("stop", "never mind")

But barge-in DOES NOT WORK because:
1. gateway.py (v1) only runs with EVO_WAKE=1, which most users don't set
2. webrtc_v2.py (v2) uses pipecat with allow_interruptions=True but no TURN server
3. The interrupt() methods exist but are never connected to actual VAD in the active pipeline

### 2b. What to build
Add a unified BargeInManager in mk2/voice/__init__.py or mk2/voice/barge_in.py:

```python
class BargeInManager:
 """Monitors microphone input during TTS playback and interrupts on speech detection."""

 def __init__(self):
 self._interrupt_cb = None
 self._vad_threshold = 0.03 # RMS energy threshold
 self._speech_frames = 0
 self._required_speech_frames = 3 # ~120ms of speech at 8kHz
 self._silence_frames = 0

 def set_interrupt_callback(self, cb):
 self._interrupt_cb = cb

 def process_frame(self, frame_bytes: bytes) -> bool:
 """Process one audio frame. Returns True if speech detected and interrupted."""
 import audioop
 rms = audioop.rms(frame_bytes, 2) / 32768.0
 if rms > self._vad_threshold:
 self._speech_frames += 1
 self._silence_frames = 0
 if self._speech_frames >= self._required_speech_frames:
 self._trigger_interrupt()
 return True
 else:
 self._silence_frames += 1
 if self._silence_frames > 10:
 self._speech_frames = 0
 return False

 def _trigger_interrupt(self):
 self._speech_frames = 0
 if self._interrupt_cb:
 try:
 self._interrupt_cb()
 except Exception as exc:
 log.warning("Barge-in interrupt callback failed: %s", exc)
```

### 2c. Wire barge-in into every voice path
1. **gateway.py (v1)**: After line 222's existing RMS check, also call BargeInManager.process_frame(). If it returns True, call self.barge_in_local().
2. **webrtc_v2.py**: The pipecat pipeline already has allow_interruptions=True. Verify it actually works by checking if SileroVADAnalyzer fires during TTS. If not, add BargeInManager to the audio stream.
3. **live.py (Gemini duplex)**: In the audio read loop, process frames through BargeInManager. If interrupted, call speaker.interrupt().
4. **convo.py**: Add barge-in support — when TTS is playing in conversation mode, monitor mic via sounddevice and interrupt on speech.

### 2d. Barge-in must work from ALL voice surfaces
The user must be able to interrupt EVO while it's speaking, regardless of which voice pipeline is active.
Test: Start speaking while EVO is mid-sentence → EVO stops immediately (<100ms) and listens.

---

## PHASE 3: DEEP RESEARCH (replace surface-level search)

### 3a. Current state
`mk2/deep_research.py` exists but just does a web search and summarizes the first page of results.
It does NOT:
- Run multiple search queries from different angles
- Scrape full article content
- Cross-reference claims across sources
- Identify conflicting information
- Produce a structured report with confidence levels

### 3b. Build DeepResearchEngine in mk2/deep_research.py
Replace or augment the existing deep_research tool with a real multi-stage research pipeline:

**Stage 1: Query decomposition**
- Take the user's question
- Generate 5-8 sub-questions that break it down
- For each sub-question, generate 3 search query variants

**Stage 2: Parallel search**
- Use web_search for each query variant
- Deduplicate results by URL
- Score results by relevance (title match, domain authority, freshness)

**Stage 3: Content extraction**
- For top 10-15 URLs, fetch full page content (not just snippets)
- Use readability extraction (html2text or equivalent) to get clean text
- Extract: title, author, date, key claims, supporting evidence

**Stage 4: Claim extraction and cross-referencing**
- For each source, extract specific factual claims
- Compare claims across sources
- Flag: consensus (multiple sources agree), conflict (sources disagree), unverified (only one source)

**Stage 5: Synthesis**
- Generate a structured report:
 - Executive summary (2-3 sentences)
 - Key findings (bulleted, each with source attribution)
 - Consensus view (what most sources agree on)
 - Conflicts (where sources disagree, both sides presented)
 - Confidence assessment (high/medium/low based on source quality and consensus)
 - Sources (URL + title + date for every source used)

**Implementation notes:**
- Use the existing web_search tool for queries
- Use the existing browser agent (Playwright) for full-page scraping when web_search snippets are insufficient
- Stream results as they come in — don't wait for all searches to complete
- Cache results for 24 hours to avoid re-searching the same topic
- Max 30 seconds total research time (timeout per stage)

### 3c. Wire into existing deep_research tool
Update the @tool("deep_research") decorator in mk2/tools/ to call the new engine.
The tool signature stays the same: `{"topic": "..."}`.
The output format changes from a summary paragraph to a structured report.

---

## PHASE 4: LATENCY REDUCTION (target: 1-2 seconds for voice, <1s for text)

### 4a. Voice pipeline latency budget
Target breakdown for voice:
- VAD finalize (silence detection): 400ms
- STT transcription: 300ms (local faster-whisper small.en or cloud Whisper)
- LLM first token: 200ms (streaming from fast model)
- TTS first audio chunk: 200ms (streaming synthesis)
- **Total to first spoken syllable: ~1.1s**

### 4b. Specific fixes

**STT:**
- In mk2/voice/stt.py, reduce finalize silence threshold from 650ms to 400ms
- Enable streaming partial results to brain immediately (don't wait for final)
- Use local faster-whisper with beam_size=1 (greedy decode, already done in commit 994cefa)

**LLM:**
- In mk2/llm.py, ensure fast model is tried FIRST, not last
- The current ladder tries primary (slow) first. Fast ladder should be tried before primary for voice.
- Enable streaming for ALL voice calls (check that stream=True is passed to llm.chat)
- Reduce token manifest size: send only last 3 messages + current user message, not full history

**TTS:**
- In mk2/voice/tts_stream.py, start synthesis BEFORE LLM response is complete
- Use sentence-chunked streaming: as LLM streams tokens, accumulate until a sentence boundary (./!/?), then synthesize that chunk immediately
- This means TTS starts playing while LLM is still generating the rest
- Target: first sentence starts playing at ~600ms, full response streams naturally

**Pipeline:**
- Create a new voice pipeline in mk2/voice/streaming.py that overlaps STT → LLM → TTS
- The flow is: user speaks → STT partial → brain starts thinking → first LLM token → TTS starts → LLM continues → more TTS chunks → done
- Every stage overlaps with the next. No stage waits for the previous one to fully complete.

### 4c. Text chat latency
- Text chat already streams via SSE. Ensure the first token hits the client within 500ms.
- Check mk2/brain.py: is handle_turn using stream=True for text? If not, add it.
- For simple queries (time, date, fast_path matches), return in <50ms without hitting the LLM.
- The fast_path in brain.py already handles this — verify it's working for all simple patterns.

### 4d. Latency measurement
Add a latency tracker to mk2/llm.py that records:
- LLM TTFT (time to first token) per model per route
- Store in a rolling 1-hour window
- Expose via /api/autonomy/health endpoint
- If TTFT exceeds 3s for the fast model, log a warning and try the next model in the ladder

---

## PHASE 5: 9/10 GATE — what 9/10 means

A system is 9/10 when ALL of these are true:
1. Model ladder uses real IDs and at least one model works without env overrides
2. No critical silent failures in kernel, brain, autonomy, bus, kill_switch
3. Permission tiers enforce actual boundaries
4. Precedent trust differentiates safe from dangerous actions
5. Awareness system works (battery, disk alerts actually fire)
6. Financial briefing doesn't crash
7. Health endpoint reports real data
8. Voice response latency < 2s to first syllable
9. Barge-in works from all voice surfaces (user interrupts EVO mid-sentence, <100ms response)
10. Deep research returns structured reports with cross-referenced sources
11. 326 tests pass, zero failures
12. No scratch files in repo
13. System can run for 24 hours without crashing or silently losing functionality
14. Money engine tracks real funnel metrics (even if no real earnings yet)

Currently at 6.5/10. After this prompt, target is 9/10.

---

## RULES

1. Do NOT modify files outside mk2/ and root-level config files
2. Do NOT break any existing test. Run `python -m pytest tests/ -q` after each phase.
3. Do NOT add external dependencies without checking requirements.txt first
4. Every new feature must be wired into the existing system (don't create orphan modules)
5. Log everything. No more bare `except: pass` in critical paths
6. If you can't implement a feature correctly, implement a degraded version that at least doesn't crash
7. Report: what you changed, what you couldn't change, test results, and the current score

## START

```bash
cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-to-9
```

Phase order: 1 (bugs) → 2 (barge-in) → 3 (deep research) → 4 (latency) → verify 9/10 gate.
