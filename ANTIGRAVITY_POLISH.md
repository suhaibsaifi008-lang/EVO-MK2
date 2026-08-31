# ANTIGRAVITY — EVO MK2 Final Polish to 9/10
## Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
## Start: git checkout -b jarvis-9-final-polish

You are the final polish agent. Previous passes built everything.
This pass fixes 4 specific remaining issues. Do not change anything else.

Score target: 9/10.

---

## ISSUE 1: MoneyIntelligence.answer() is dead code

File: mk2/money_intelligence.py has a complete `answer()` method (lines 95-109)
that does direct LLM reasoning about money with full context. It is NEVER called.

File: mk2/brain.py line 525-540 — currently injects `mi.get_context()` into
the normal tool-calling flow. This works but is clunky: the LLM gets data
but still goes through multi-step tool orchestration.

Fix brain.py: use `mi.answer(question)` directly
instead of injecting context into the tool-calling flow.

How to decide: if the user's message + money keywords is a question about
their business state (how am I doing, what should I do, show me my numbers,
am I on track), use `mi.answer()` for a direct, conversational reply.
If the user asks to DO something (send a proposal, create an invoice,
record a payment), use the normal tool-calling flow.

Add a helper in brain.py:

def _is_money_analysis_question(text: str) -> bool:
 """Return True if this is an analysis/recommendation question, not an action."""
 analysis_indicators = (
 "how am i doing", "how's my business", "what should i", "show me my",
 "am i on track", "how much", "what's my", "my numbers", "my stats",
 "overview", "briefing", "summary", "how's it going", "how is it going",
 "what do you think", "recommend", "advice", "insight", "analysis",
 "doing well", "doing bad", "improve", "better at",
 )
 action_indicators = (
 "send", "create", "submit", "record", "add", "delete", "update",
 "invoice", "proposal", "payment", "follow up", "contact",
 )
 lower = text.lower()
 if any(a in lower for a in action_indicators):
 return False
 if any(a in lower for a in analysis_indicators):
 return True
 # Default: if it's a question about money, treat as analysis
 return any(k in lower for k in money_keywords) and "?" in text

Then in handle_turn(), around line 525:

if money_keywords_hit and _is_money_analysis_question(text):
 try:
 from .money_intelligence import get_money_intelligence
 reply = get_money_intelligence().answer(text)
 emit({"type": "done", "text": reply})
 return reply
 except Exception:
 pass # fall through to normal tool-calling flow

This gives the LLM a direct reasoning path for money questions.

---

## ISSUE 2: Proactive learning blocks the response

File: mk2/brain.py, lines 822-829

Current: deep_research(text) called directly in handle_turn().
This blocks the entire turn for 30-90 seconds.

Fix: run it in a background thread.

Change:
 from .research_tools import deep_research
 deep_research(text)

To:
 import threading
 threading.Thread(
 target=lambda: __import__("mk2.research_tools", fromlist=["deep_research"]).deep_research(text),
 daemon=True,
 name="mk2-auto-research"
 ).start()

And add a reply prefix so the user knows it's happening:
Before the uncertainty check, add:
 answer_prefix = ""
 if uncertainty_hit:
 answer_prefix = "I didn't have a great answer for that — I'm researching it now. "

Then prepend answer_prefix to the final reply.

The research happens in the background. The user gets an immediate reply.
Next time they ask, the knowledge is already in the vault.

---

## ISSUE 3: Skill matching is keyword overlap

File: mk2/skills.py, lines 255-273, method get_relevant_skills()

Current: simple word intersection. "python" matches "python" but
misses "programming in Python", "coding", "build a web app".

Fix: add fuzzy matching using difflib (stdlib, no new deps).

Replace the matching logic:
 def get_relevant_skills(self, context: str) -> list[dict[str, Any]]:
 relevant = []
 try:
 import difflib
 ctx_tokens = set(re.findall(r"\w{3,}", context.lower()))
 for f in self.skills_dir.glob("*.json"):
 try:
 skills_list = json.loads(f.read_text(encoding="utf-8"))
 for skill in skills_list:
 proc = skill.get("procedure", "").lower()
 topic = skill.get("topic", "").lower()
 combined_text = f"{topic} {proc}"
 skill_tokens = set(re.findall(r"\w{3,}", combined_text))

 # 1. Exact token overlap (current behavior)
 exact = len(ctx_tokens & skill_tokens)

 # 2. Fuzzy match: check if any context token is close to any skill token
 fuzzy = 0
 for ct in ctx_tokens:
 for st in skill_tokens:
 if len(ct) > 3 and len(st) > 3:
 if difflib.SequenceMatcher(None, ct, st).ratio() > 0.8:
 fuzzy += 1

 score = exact + fuzzy * 0.5
 if score > 0:
 entry = dict(skill)
 entry["_match_score"] = score
 relevant.append(entry)
 except Exception:
 pass
 # Sort by match score descending
 relevant.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
 except Exception:
 pass
 return relevant[:5] # max 5 skills

This catches: "programming" ≈ "programing", "app" ≈ "apps", "build" ≈ "building".
No external dependencies. Pure stdlib.

---

## ISSUE 4: Money briefing doesn't push to user

File: mk2/money_briefing.py line 96 — publishes bus event but nothing listens.

File: mk2/jarvis_agent.py — daily tick generates briefing but only logs it.

Fix: wire the briefing to actually reach the user.

Step 1: In mk2/jarvis_agent.py, after generating the briefing (line 140),
if there are urgent items, publish a notification:

if m_brief.get("top_actions"):
 urgent = [a for a in m_brief["top_actions"] if any(
 k in a.lower() for k in ("overdue", "payment", "urgent", "due today")
 )]
 if urgent:
 bus.publish("notify.out", {
 "kind": "money_briefing",
 "text": f"Money briefing: {len(urgent)} urgent items. {urgent[0][:100]}",
 "priority": "high",
 })

Step 2: In mk2/brain.py, add a bus listener that surfaces briefing
when the user first interacts in the morning (if briefing was generated
in the last 4 hours and hasn't been shown).

Actually, simpler approach: just make the JarvisAgent findings list
include the briefing text, and the brain's daily findings response
already speaks to the user. Check how findings are communicated to user.

Look at jarvis_agent.py for how "findings" are surfaced to the user.
The findings list is built but may not be spoken. If it's logged only,
add a bus.publish("notify.out", ...) for each finding.

---

## VERIFICATION

After all fixes:
1. Run python -m pytest tests/ -q — must still pass (395+ tests)
2. Verify money_intelligence.answer() is called from brain.py
3. Verify deep_research is in a daemon thread, not blocking
4. Verify difflib is used in skills.py get_relevant_skills()
5. Verify bus.publish("notify.out") exists for money briefing

Report: what changed, test results, final score.

---

## RULES

1. Working directory: C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
2. Modify ONLY the 4 files: mk2/brain.py, mk2/skills.py, mk2/money_briefing.py, mk2/jarvis_agent.py
3. Do NOT break any test
4. Do NOT add external dependencies
5. No bare except: pass
6. If something is unclear, make the best choice and note it

## START

cd C:\Users\MOHD SUHAIB\Downloads\EVO-MK2
git checkout -b jarvis-9-final-polish
