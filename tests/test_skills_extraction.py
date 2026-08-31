import json
import pytest
from mk2.skills import SkillExtractor, get_skill_extractor


@pytest.fixture
def extractor_instance(tmp_path):
    return SkillExtractor(skills_dir=tmp_path / "skills")


def test_extract_from_research(extractor_instance, monkeypatch):
    mock_llm_response = (
        "1. Hook viewers within first 3 seconds with high-contrast text overlay.\n"
        "2. Publish at 2pm on weekdays to maximize peak audience engagement.\n"
        "3. Maintain thumbnail color contrast ratio greater than 70%."
    )
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: mock_llm_response)

    skills = extractor_instance.extract_from_research("YouTube Channel Growth", "Detailed research about retention curves and CTR.")
    assert len(skills) == 3
    assert skills[0]["topic"] == "YouTube Channel Growth"
    assert "Hook viewers" in skills[0]["procedure"]
    assert "2pm" in skills[1]["procedure"]


def test_get_relevant_skills(extractor_instance, monkeypatch):
    mock_llm_response = "1. Always sanitize database inputs with parameterized queries."
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: mock_llm_response)
    extractor_instance.extract_from_research("SQL Injection Prevention", "Security research.")

    relevant = extractor_instance.get_relevant_skills("How do I protect my SQL database against injection attacks?")
    assert len(relevant) >= 1
    assert "parameterized queries" in relevant[0]["procedure"]


def test_no_actionable_skills_returned(extractor_instance, monkeypatch):
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: "None identified")
    skills = extractor_instance.extract_from_research("Abstract Philosophy", "Existential musings.")
    assert skills == []


def test_skills_disk_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: "1. Follow the test-driven development cycle: Red, Green, Refactor.")
    ext1 = SkillExtractor(skills_dir=tmp_path / "skills")
    ext1.extract_from_research("TDD Methodology", "Software testing practices.")

    ext2 = SkillExtractor(skills_dir=tmp_path / "skills")
    relevant = ext2.get_relevant_skills("software testing and TDD")
    assert len(relevant) >= 1
    assert "Red, Green, Refactor" in relevant[0]["procedure"]


def test_fuzzy_skill_matching(extractor_instance, monkeypatch):
    mock_llm_response = "1. Build responsive user interfaces with flexbox and grid layouts."
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: mock_llm_response)
    extractor_instance.extract_from_research("Frontend Web Development", "UI layout guide.")

    # "building web applications" has fuzzy similarity to "build", "web", "layout"
    relevant = extractor_instance.get_relevant_skills("building web applications with modern layouts")
    assert len(relevant) >= 1
    assert "responsive user interfaces" in relevant[0]["procedure"]


def test_get_skill_extractor_singleton():
    e1 = get_skill_extractor()
    e2 = get_skill_extractor()
    assert e1 is e2
