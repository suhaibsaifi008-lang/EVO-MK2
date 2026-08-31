import pytest
from mk2.knowledge import KnowledgeSynthesizer, get_knowledge_synthesizer


@pytest.fixture
def synth_instance(tmp_path, monkeypatch):
    class MockKnowledgeAgent:
        def search(self, query, limit=5):
            if "quantum" in query.lower():
                return [{"title": "cryptography", "snippet": "RSA encryption notes", "score": 0.85}]
            return []

    mock_ka = MockKnowledgeAgent()
    synth = KnowledgeSynthesizer(ka=mock_ka)
    synth.graph_file = tmp_path / "knowledge_graph.json"
    synth._graph = {}
    return synth


def test_on_new_research_connections(synth_instance, monkeypatch):
    monkeypatch.setattr("mk2.llm.chat", lambda messages, **kw: "- Post-Quantum Algorithms\n- Quantum Key Distribution")
    res = synth_instance.on_new_research("Quantum Computing", "Summary of qubits, quantum superposition, and RSA breaking.")
    assert res["topic"] == "Quantum Computing"
    assert res["new_entries"] >= 2
    assert any(c["existing_topic"] == "cryptography" for c in res["connections"])
    assert any("Quantum" in c["existing_topic"] for c in res["connections"])


def test_get_related_knowledge(synth_instance):
    synth_instance._graph["AI Models"] = [{"existing_topic": "Transformers", "relevance": 0.9, "connection_type": "llm_inferred"}]
    related = synth_instance.get_related("AI Models")
    assert len(related) >= 1
    assert any(r["title"] == "Transformers" for r in related)


def test_graph_persistence(tmp_path):
    class MockKnowledgeAgent:
        def search(self, query, limit=5):
            return []

    synth1 = KnowledgeSynthesizer(ka=MockKnowledgeAgent())
    synth1.graph_file = tmp_path / "graph.json"
    synth1._graph["TopicA"] = [{"existing_topic": "TopicB", "relevance": 0.8, "connection_type": "direct"}]
    synth1._save_graph()

    synth2 = KnowledgeSynthesizer(ka=MockKnowledgeAgent())
    synth2.graph_file = tmp_path / "graph.json"
    synth2._load_graph()
    assert "TopicA" in synth2._graph
    assert synth2._graph["TopicA"][0]["existing_topic"] == "TopicB"


def test_get_knowledge_synthesizer_singleton():
    s1 = get_knowledge_synthesizer()
    s2 = get_knowledge_synthesizer()
    assert s1 is s2
