"""Governance (agent cards in sync with the code) and evaluation quality gates."""

from app import __version__
from app.agents.graph import NODES
from app.agents.tools import TOOLS_BY_NAME
from app.evaluation.run_eval import run_all
from app.governance import load_cards, validate_cards


def test_agent_cards_are_complete_and_up_to_date():
    assert validate_cards(load_cards(), list(NODES), __version__) == []


def test_agent_card_tools_exist():
    extra = {"knowledge_base.search", "actions.propose", "run_store", "metrics", "incident_manager"}
    for card in load_cards()["agents"]:
        for tool in card["tools"]:
            assert tool in TOOLS_BY_NAME or tool in extra, f"{card['id']} references unknown tool {tool}"


def test_validator_detects_drift():
    cards = load_cards()
    problems = validate_cards(cards, [*NODES, "new_agent"], "9.9.9")
    assert any("new_agent" in p for p in problems) and any("version" in p for p in problems)


def test_agent_card_endpoint(client):
    body = client.get("/api/agent-card").json()
    assert body["validation"]["problems"] == []
    assert "supervisor" in body["graph_mermaid"]
    assert client.get("/api/agent-card/monitor").json()["role"]


def test_evaluation_quality_gates():
    report = run_all(with_latency=False)
    assert report["routing"]["intent_accuracy"] >= 0.9
    assert report["routing"]["site_accuracy"] >= 0.9
    assert report["anomaly_detection"]["f1"] >= 0.9
    assert report["retrieval"]["hit_rate_at_3"] >= 0.9
    assert report["guardrails"]["accuracy"] == 1.0
