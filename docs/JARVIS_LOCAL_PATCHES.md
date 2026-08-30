# JARVIS — Local Patches & Ops Runbook

Patches we carry inside `C:\Users\MOHD SUHAIB\AppData\Local\hermes\hermes-agent`
(the upstream clone). `hermes update` stashes local changes automatically
(`updates.non_interactive_local_changes: stash`) — after an update, re-apply:

```powershell
cd C:\Users\MOHD SUHAIB\AppData\Local\hermes\hermes-agent
git stash pop          # or cherry-pick; resolve conflicts, then commit locally
```

Both are upstream-PR candidates (Phase 10 of JARVIS_ROADMAP.md).

| # | File | Change | Why |
|---|------|--------|-----|
| 1 | `tools/voice_mode.py` | `PLAYBACK_MIN_TRIGGER = 1500.0 → 400.0` | 1500 RMS is shout-level; barge-in unreachable on normal mic gain. Bleed measured ~26–40 RMS on this machine, so 400 keeps anti-bleed margin. |
| 2 | `cli.py` (`_on_wake_word`, ~line 15407) | Honor `voice.auto_tts` in wake handler, mirroring `/voice on` gate | Upstream bug: wake-triggered turns were always text-only unless `/voice tts` typed manually. |

## Config rollback repo

`C:\Users\MOHD SUHAIB\AppData\Local\hermes\.git` tracks `config.yaml`, `SOUL.md`,
`memories/`. **NEVER push** (contains FreeLLMAPI key). Commit before/after every
config experiment.

## Ops tools

- `jarvis_lane_doctor.py` — probe FreeLLMAPI lane health; `--apply` promotes the
  fastest alive lane to primary. Run whenever replies feel slow, then restart the
  hermes session (fallback state is sticky per session).
- Ollama floor: last fallback in chain (`gemma4:latest @ localhost:11434`) —
  proxy outages degrade to slow-local instead of dead.

## Known behavior

- FreeLLMAPI lanes flap on a minutes scale — one "switched to fallback" line per
  turn occasionally is normal and self-healing.
- First voice/wake call after boot is slow (model cold-load); subsequent calls fast.
