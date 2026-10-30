import pytest

from app.agents.nodes import _sanitize_decision, detect_days, detect_site, next_step, rule_route
from app.agents.state import RoutingDecision


@pytest.mark.parametrize(("query", "intent", "site"), [
    ("What is the consumption of the Kenitra plant?", "consumption", "PLANT-KEN"),
    ("Any anomalies in Rabat?", "anomaly", "DC-RABAT"),
    ("How can I reduce costs at the clinic?", "advice", "CLINIC-MRK"),
    ("Full monthly report for the office", "report", "HQ-CASA"),
])
def test_rule_router(query, intent, site):
    d = rule_route(query)
    assert d.intent == intent and d.site_id == site


def test_windows():
    assert detect_days("last 14 days") == 14
    assert detect_days("this week") == 7
    assert detect_days("yesterday") == 1
    assert detect_days("this month") == 30
    assert detect_days("in general") is None


def test_site_hint_used_when_question_has_no_site():
    assert rule_route("consumption this week", site_hint="DC-RABAT").site_id == "DC-RABAT"
    assert detect_site("hello") is None


def test_llm_output_is_sanitized():
    raw = RoutingDecision(intent="report", agents=["energy_advisor", "consumption_analyst", "energy_advisor"],
                          site_id=None, days=30)
    d = _sanitize_decision(raw, None, "report for the clinic")
    assert d.agents == ["consumption_analyst", "energy_advisor"]  # deduplicated + ordered
    assert d.site_id == "CLINIC-MRK"


def test_dispatcher_follows_the_plan():
    state = {"plan": ["consumption_analyst", "energy_advisor"], "completed": ["guardrails", "supervisor"]}
    assert next_step(state) == "consumption_analyst"
    state["completed"].append("consumption_analyst")
    assert next_step(state) == "energy_advisor"
    state["completed"].append("energy_advisor")
    assert next_step(state) == "report_writer"
