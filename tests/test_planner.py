import pytest
from mk2 import planner


def test_should_plan_detection():
    # Simple questions should NOT trigger planning
    assert not planner.should_plan("what time is it")
    assert not planner.should_plan("who is the president")
    assert not planner.should_plan("what is 2 + 2")

    # Multi-step complex tasks SHOULD trigger planning
    assert planner.should_plan("Plan and build a project structure for a microservice")
    assert planner.should_plan("Research and compare 3 cloud providers and write a report")
    assert planner.should_plan("Automate the deployment pipeline and audit logs")
    assert planner.should_plan("Create folder src and then write main.py and then test it")


def test_plan_dataclasses():
    step1 = planner.PlanStep(id=1, description="Search documentation", tool_hint="web_search")
    step2 = planner.PlanStep(id=2, description="Write summary", depends_on=[1])
    p = planner.Plan(goal="Test goal", steps=[step1, step2], rationale="Test rationale")

    assert len(p.steps) == 2
    assert p.steps[1].depends_on == [1]
    assert p.replan_count == 0


def test_extract_json_object():
    valid_raw = '```json\n{"steps": [{"id": 1, "description": "do X"}], "rationale": "ok"}\n```'
    data = planner._extract_json_object(valid_raw)
    assert isinstance(data, dict)
    assert len(data["steps"]) == 1
    assert data["rationale"] == "ok"
