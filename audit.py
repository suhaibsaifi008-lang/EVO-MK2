"""Full MK2 audit: find every bug, dead path, and broken connection."""
import ast
import importlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, ".")

print("=" * 60)
print("EVO MK2 FULL AUDIT")
print("=" * 60)

# ---- 1. Import every module, catch failures ----
print("\n[1] MODULE IMPORTS")
modules = [
    "mk2.config", "mk2.bus", "mk2.db", "mk2.llm", "mk2.brain",
    "mk2.tools", "mk2.memory", "mk2.server", "mk2.kernel",
    "mk2.vault", "mk2.diag", "mk2.errlog", "mk2.reminders",
    "mk2.fastlane", "mk2.research_tools",
    "mk2.voice.stt", "mk2.voice.tts", "mk2.voice.wake",
    "mk2.voice.grammar_rescue", "mk2.voice.live", "mk2.voice.gateway",
]
for m in modules:
    try:
        importlib.import_module(m)
        print(f"  OK   {m}")
    except Exception as e:
        print(f"  FAIL {m}: {e}")

# ---- 2. Tool registry completeness ----
print("\n[2] TOOL REGISTRY")
from mk2 import tools
tools.ensure_loaded()
manifest = tools.manifest()
print(f"  {len(manifest)} tools registered:")
for t in manifest:
    print(f"    {t['name']}")
    # verify fn is callable
    tool_obj = tools._REGISTRY.get(t["name"])
    if not callable(tool_obj.fn):
        print(f"      WARNING: fn not callable!")

# ---- 3. Provider connectivity ----
print("\n[3] PROVIDER CONNECTIVITY")
from mk2 import llm
for p in llm._providers():
    print(f"  provider: {p['name']} base={p['base']} model={p['default_model']}")
    try:
        result = llm._hard_bounded(
            lambda: llm._completion(p["base"], p["key"],
                {"model": p["default_model"],
                 "messages": [{"role": "user", "content": "Say ONLINE"}]},
                timeout=15),
            20)
        content = result["choices"][0]["message"]["content"].strip()
        print(f"    -> '{content[:50]}' ({len(content)} chars)")
        if not content:
            print(f"    -> EMPTY RESPONSE (bug!)")
    except Exception as e:
        print(f"    -> FAIL: {str(e)[:200]}")

# ---- 4. Web search connectivity ----
print("\n[4] WEB SEARCH")
from mk2.tools.web_tools import ddg_results, fetch_page_text
try:
    ddg = ddg_results("best laptops under 50000", max_results=3)
    print(f"  DDG: {len(ddg)} results")
except Exception as e:
    print(f"  DDG FAIL: {e}")


# ---- 5. Research pipeline step-by-step ----
print("\n[5] RESEARCH PIPELINE")
from mk2.research_tools import _gather_sources
t0 = __import__("time").time()
sources = _gather_sources("best laptop under 50000")
dt = __import__("time").time() - t0
print(f"  gather: {len(sources)} sources in {dt:.1f}s")
for s in sources:
    print(f"    {s['url'][:70]} ({len(s['text'])} chars)")

if not sources:
    print("  BUG: zero sources gathered! Deep research cannot work.")

# test synthesis
if sources:
    from mk2 import llm as llm_mod
    material = "\n\n".join(f"[{i+1}] {s['url']}\n{s['text'][:800]}" for i, s in enumerate(sources))[:6000]
    t0 = time.time()
    try:
        report = llm.chat([
            {"role": "system", "content": "Write a brief research summary."},
            {"role": "user", "content": f"Topic: best laptop under 50000\n\nMaterial:\n{material}"},
        ], role="primary", temperature=0.3, timeout=45)
        dt = time.time() - t0
        print(f"  synthesis: {len(report)} chars in {dt:.1f}s")
        if not report.strip():
            print("  BUG: empty synthesis!")
    except Exception as e:
        print(f"  SYNTHESIS FAIL: {str(e)[:300]}")

# ---- 6. Reminder system end-to-end ----
print("\n[6] REMINDER SYSTEM")
from mk2 import db, reminders
db.migrate()
fired = []
reminders.tick(lambda topic, payload: fired.append(payload))
print(f"  pending: {len(db.reminders_pending())}, fired now: {len(fired)}")

# ---- 7. Fast-lane coverage gaps ----
print("\n[7] FAST-LANE COVERAGE GAPS")
from mk2.fastlane import fast_command

test_cases = [
    ("open youtube", True),
    ("open wikipedia", True),
    ("what time is it", True),
    ("screenshot", True),
    ("volume up", True),
    ("search for python tutorials", True),
    ("research AI trends", True),
    ("remind me to stretch in 5 minutes", True),
    ("whats on my screen right now", True),
    ("play lofi beats", True),
    ("tell me a joke", False),           # should go to LLM
    ("explain quantum computing", False), # should go to LLM
]
for text, should_resolve in test_cases:
    try:
        r = fast_command(text)
        resolved = r is not None
        status = "OK" if resolved == should_resolve else "GAP"
        if status == "GAP":
            print(f"  GAP: '{text}' -> {'resolved' if resolved else 'not resolved'} (expected {'resolved' if should_resolve else 'not resolved'})")
    except Exception as e:
        print(f"  ERROR on '{text}': {e}")

# ---- 8. Screen capture timing ----
print("\n[8] SCREEN CAPTURE TIMING")
try:
    from mk2.work_tools import _capture_png
    t0 = time.time()
    png = _capture_png()
    dt = time.time() - t0
    size = png.stat().st_size
    print(f"  capture: {dt:.1f}s, {size/1024:.0f}KB -> {png.name}")
except Exception as e:
    print(f"  CAPTURE FAIL: {e}")

# ---- 9. Vault integrity ----
print("\n[9] VAULT")
from mk2 import vault
notes = vault.list_notes()
print(f"  notes: {len(notes)}")
for n in notes[:5]:
    print(f"    {n['file']} ({n['size']}b)")

# ---- 10. Error ring ----
print("\n[10] RECENT ERRORS")
from mk2 import errlog
errs = errlog.recent(10)
if errs:
    for e in errs[-5:]:
        print(f"  [{e['component']}] {e['detail'][:120]}")
else:
    print("  (none logged)")

print("\n" + "=" * 60)
print("AUDIT COMPLETE")
