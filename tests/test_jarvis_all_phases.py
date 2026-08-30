"""Comprehensive Unit Tests for all 13 phases of the JARVIS Autonomous Architecture."""
import time
from mk2.comms_agent import CommsAgent
from mk2.comms_intelligence import CommsIntelligence
from mk2.schedule_agent import ScheduleAgent
from mk2.knowledge_agent import KnowledgeAgent
from mk2.file_agent import FileAgent
from mk2.research_agent import ResearchAgent
from mk2.synthesis import SynthesisEngine
from mk2.security_agent import SecurityAgent
from mk2.wellness_agent import WellnessAgent
from mk2.kill_switch import KillSwitch
from mk2.jarvis_agent import JarvisAgent
from mk2.strategy_learner import StrategyLearner
from mk2.preference_learner import PreferenceLearner


def test_comms_agent_and_intelligence():
    ca = CommsAgent()
    ci = CommsIntelligence()
    
    msgs = [
        {"id": "m1", "from": "Bob", "text": "Can we talk later?"},
        {"id": "m2", "from": "Infra Alert", "text": "URGENT: server down, database outage!"},
    ]
    ranked = ca.prioritize_messages(msgs)
    assert len(ranked) == 2
    assert ci.detect_urgent(ranked[0])
    assert not ci.detect_urgent(ranked[1])
    
    # Draft reply
    v_reply = ca.draft_reply({"from": "Alice", "text": "Are you available?"})
    assert v_reply.verdict == "safe"
    assert "reply_text" in v_reply.action


def test_schedule_agent():
    sa = ScheduleAgent()
    now = time.time()
    eid = sa.add_event("Design Review", now + 1000, now + 3000, attendees=["lead@studio.com"])
    assert eid.startswith("ev_")
    
    upcoming = sa.get_upcoming_events(24)
    assert any(e["id"] == eid for e in upcoming)
    
    prep = sa.pre_meeting_prep({"id": eid, "title": "Design Review", "attendees": ["lead@studio.com"]})
    assert prep["prep_ready"]
    assert len(prep["briefing"]) > 10


def test_knowledge_and_file_agents():
    ka = KnowledgeAgent()
    results = ka.search("python", limit=5)
    assert isinstance(results, list)
    
    tags = ka.auto_tag("Autonomous AI agents with Playwright browser automation")
    assert isinstance(tags, list)
    assert len(tags) > 0

    fa = FileAgent()
    summary = fa.summarize_batch(["README.md"])
    assert isinstance(summary, str)


def test_research_and_synthesis():
    ra = ResearchAgent()
    tid = ra.monitor_topic("Local AI Architectures")
    assert tid.startswith("top_")
    assert "Local AI Architectures" in ra.list_monitored_topics()
    
    se = SynthesisEngine()
    dots = se.connect_dots([
        {"source": "calendar", "event": "Meeting tomorrow on AI infrastructure"},
        {"source": "email", "body": "Client needs local LLM latency below 2 seconds"},
    ])
    assert isinstance(dots, list)


def test_security_and_wellness():
    sec = SecurityAgent()
    v_clean = sec.check_phishing({"subject": "Meeting reminder for 3pm"})
    assert v_clean.verdict == "safe"
    
    v_phish = sec.check_phishing({"subject": "URGENT: account has been suspended click here to unlock"})
    assert v_phish.verdict == "caution"
    assert "phishing_threat" in v_phish.risks
    
    wel = WellnessAgent()
    st = wel.track_screen_time()
    assert "current_session_minutes" in st
    assert "is_late_night" in st


def test_kill_switch_and_jarvis_brain():
    ks = KillSwitch()
    halt = ks.stop_all("Automated Test Stop")
    assert halt["ok"]
    assert halt["latency_ms"] < 50
    assert halt["status"] == "halted"

    ja = JarvisAgent()
    tick_out = ja.tick()
    assert tick_out["ok"]
    assert isinstance(tick_out["findings"], list)


def test_learning_subsystems():
    sl = StrategyLearner()
    sl.record_outcome("freelance_pitches", "direct_pitch", True, reward=500.0)
    best = sl.get_best_strategy("freelance_pitches")
    assert best["best_strategy"] == "direct_pitch"
    assert best["wins"] >= 1
    
    pl = PreferenceLearner()
    pl.record_feedback("email_tone", {"tone": "crisp_bulleted"})
    pref = pl.get_preference("email_tone")
    assert pref["tone"] == "crisp_bulleted"
