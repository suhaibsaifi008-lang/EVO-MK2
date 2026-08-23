from pathlib import Path

p = Path("mk2/brain.py")
s = p.read_text(encoding="utf-8")

# Replace the entire fast-lane block with clean compound handling
start = s.index("    # Fast-lane: obvious commands execute instantly, zero model calls.")
end = s.index('        emit({"type": "done", "text": instant})')
end = s.index("\n", end) + 1

new_block = '''    # Fast-lane: single obvious commands execute instantly, zero model calls.
    # Compound commands ("open X and search Y") go to the agent, which has the
    # same tools and handles multi-step sequencing naturally.
    from .fastlane import fast_command, _split_compound

    parts = _split_compound(text)
    if len(parts) == 1:
        instant = fast_command(text)
        if instant:
            emit({"type": "done", "text": instant})
            memory.record_turn(text, instant, surface)
            db.trace(turn_id, "total_fastlane", (time.time() - t0) * 1000)
            bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": instant})
            return instant
    elif all(fast_command(p) is not None or True for p in []):
        pass  # never reached, kept for readability
'''

s = s[:start] + new_block + s[end:]
p.write_text(s, encoding="utf-8")
print("clean compound block written")
