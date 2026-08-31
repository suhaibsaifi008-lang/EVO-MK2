import pytest
from mk2.opportunity_scorer import OpportunityScorer, ScoredOpportunity, get_opportunity_scorer


@pytest.fixture
def scorer_instance(tmp_path, monkeypatch):
    from mk2 import opportunity_scorer
    monkeypatch.setattr(opportunity_scorer, "MODEL_FILE", tmp_path / "opportunity_weights.json")
    return OpportunityScorer()


def test_calculate_win_probability(scorer_instance):
    opp = {
        "title": "Senior Python AI Agent Developer",
        "description": "Looking for FastAPI LLM automation bot engineer",
        "budget": 1500.0,
        "client_spent": 10000.0,
        "proposals_count": 4,
    }
    prob, breakdown = scorer_instance.calculate_win_probability(opp)
    assert 0.05 <= prob <= 0.95
    assert prob >= 0.60
    assert "skill_score" in breakdown


def test_score_opportunity_ev(scorer_instance):
    opp = {"id": "job_ev_1", "title": "Python Scraper", "budget": 1000.0}
    scored = scorer_instance.score_opportunity(opp)
    assert isinstance(scored, ScoredOpportunity)
    assert scored.expected_value == round(scored.win_probability * 1000.0, 2)
    assert scored.budget == 1000.0


def test_record_outcome_weight_adaptation(scorer_instance):
    for i in range(6):
        scorer_instance.record_outcome(f"job_{i}", won=False, actual_revenue=0.0)
    assert scorer_instance.weights["skill_match"] >= 0.35


def test_rank_opportunities(scorer_instance):
    opps = [
        {"id": "low", "title": "Low budget entry", "budget": 50.0, "proposals_count": 30},
        {"id": "high", "title": "Python AI Agent Specialist", "budget": 2500.0, "client_spent": 20000.0, "proposals_count": 2},
    ]
    ranked = scorer_instance.rank_opportunities(opps)
    assert len(ranked) == 2
    assert ranked[0].id == "high"
    assert ranked[0].expected_value > ranked[1].expected_value


def test_skill_match_heuristics(scorer_instance):
    opp_generic = {"title": "General Virtual Assistant", "budget": 500.0}
    opp_expert = {"title": "Python FastAPI LLM Agent Developer", "budget": 500.0}
    prob_gen, _ = scorer_instance.calculate_win_probability(opp_generic)
    prob_exp, _ = scorer_instance.calculate_win_probability(opp_expert)
    assert prob_exp > prob_gen


def test_budget_sweetspot_scoring(scorer_instance):
    opp_sweet = {"title": "Python Task", "budget": 1000.0}
    opp_micro = {"title": "Python Task", "budget": 10.0}
    prob_sweet, b_sweet = scorer_instance.calculate_win_probability(opp_sweet)
    prob_micro, b_micro = scorer_instance.calculate_win_probability(opp_micro)
    assert b_sweet["budget_score"] > b_micro["budget_score"]


def test_client_reputation_factor(scorer_instance):
    opp_unverified = {"title": "Python Task", "budget": 500.0, "client_spent": 0.0, "payment_verified": False}
    opp_verified = {"title": "Python Task", "budget": 500.0, "client_spent": 15000.0, "payment_verified": True}
    _, b_unv = scorer_instance.calculate_win_probability(opp_unverified)
    _, b_v = scorer_instance.calculate_win_probability(opp_verified)
    assert b_v["reputation_score"] > b_unv["reputation_score"]


def test_competition_count_factor(scorer_instance):
    opp_crowded = {"title": "Python Task", "budget": 500.0, "proposals_count": 45}
    opp_early = {"title": "Python Task", "budget": 500.0, "proposals_count": 2}
    _, b_crowd = scorer_instance.calculate_win_probability(opp_crowded)
    _, b_early = scorer_instance.calculate_win_probability(opp_early)
    assert b_early["competition_score"] > b_crowd["competition_score"]


def test_empty_opportunity_ranking(scorer_instance):
    assert scorer_instance.rank_opportunities([]) == []


def test_priority_recommendation_classification(scorer_instance):
    opp_high = {"title": "Python LLM Bot", "budget": 3000.0, "client_spent": 10000.0, "proposals_count": 2}
    scored = scorer_instance.score_opportunity(opp_high)
    assert scored.recommendation in ("high_priority", "bid")
