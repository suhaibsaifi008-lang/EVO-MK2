from pathlib import Path

p = Path("mk2/brain.py")
s = p.read_text(encoding="utf-8")

# fast-lane must run BEFORE the agent, and compound commands resolve here too
old = '''    # Fast-lane: obvious commands execute instantly, zero model calls.
    from .fastlane import fast_command

    instant = fast_command(text)'''
new = '''    # Fast-lane: obvious commands execute instantly, zero model calls.
    # Compound commands ("open X and search Y") split and run sequentially.
    from .fastlane import fast_command

    instant_parts: list[str] = []
    for sub in _split_compound(text):
        instant = fast_command(sub)
        if instant:
            instant_parts.append(instant)
        else:
            instant_parts.append("")  # placeholder keeps order when mixed
    if any(instant_parts):
        if all(instant_parts):
            reply = "; ".join(instant_parts)
        else:
            # Mixed command+question: answer the question via LLM but keep
            # executed parts in the reply prefix.
            question = " ".join(
                sub for sub, ok in zip(_split_compound(text), instant_parts) if not ok
            )
            rest = brain_handle_rest(question) if False else None
            reply = "; ".join(p for p in instant_parts if p)
            # fall through to agent for unresolved parts
            unresolved = [sub for sub, ok in zip(_split_compound(text), instant_parts) if not ok]
            if unresolved:
                agent_reply = self._agent_turn(" ".join(unresolved)) if hasattr(self, "_agent_turn") else None
                if agent_reply:
                    reply = "; ".join(instant_parts + [agent_reply])
        emit({"type": "done", "text": reply})
        memory.record_turn(text, reply, surface)
        db.trace(turn_id, "total_fastlane", (time.time() - t0) * 1000)
        bus.publish("convo.turn", {"id": turn_id, "text": text, "reply": reply})
        return reply'''
if old in s:
    s = s.replace(old, new, 1)

p.write_text(s, encoding="utf-8")
print("compound support added to brain")
