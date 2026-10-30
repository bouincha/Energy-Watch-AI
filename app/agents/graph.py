"""LangGraph wiring of the EnergyWatch AI multi-agent system (supervisor pattern)."""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import (
    after_guardrails,
    anomaly_detector_node,
    consumption_analyst_node,
    energy_advisor_node,
    guardrails_node,
    monitor_node,
    next_step,
    report_writer_node,
    supervisor_node,
)
from app.agents.state import SPECIALISTS, EnergyState
from app.core.observability import (
    get_correlation_id,
    log_event,
    metrics,
    reset_correlation_id,
    runs,
    sanitize_correlation_id,
    set_correlation_id,
)
from app.llm.gateway import get_gateway

logger = logging.getLogger("energywatch.graph")

NODES = {
    "guardrails": guardrails_node,
    "supervisor": supervisor_node,
    "consumption_analyst": consumption_analyst_node,
    "anomaly_detector": anomaly_detector_node,
    "energy_advisor": energy_advisor_node,
    "report_writer": report_writer_node,
    "monitor": monitor_node,
}


def build_graph():
    g = StateGraph(EnergyState)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    g.add_edge(START, "guardrails")
    g.add_conditional_edges("guardrails", after_guardrails, ["supervisor", "monitor"])
    targets = [*SPECIALISTS, "report_writer"]
    g.add_conditional_edges("supervisor", next_step, targets)
    for agent in SPECIALISTS:
        g.add_conditional_edges(agent, next_step, targets)
    g.add_edge("report_writer", "monitor")
    g.add_edge("monitor", END)
    return g.compile()


@lru_cache
def get_graph():
    return build_graph()


def graph_mermaid() -> str:
    return get_graph().get_graph().draw_mermaid()


def run_agent(query: str, site_id: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
    """Execute the multi-agent graph once. Always returns a response carrying the correlation ID."""
    cid = sanitize_correlation_id(correlation_id or get_correlation_id())
    token = set_correlation_id(cid)
    gw = get_gateway()
    run = runs.start(cid, query, gw.provider)
    log_event(logger, "run started", site_id=site_id, provider=gw.provider)
    try:
        state = get_graph().invoke({
            "correlation_id": cid, "query": query, "site_id": site_id, "started_perf": time.perf_counter(),
            "completed": [], "trace": [], "errors": [], "degraded": False,
        })
    except Exception as exc:
        logger.exception("run crashed")
        run.status = "error"
        run.health = {"status": "error", "error": str(exc)}
        metrics.inc("energywatch_runs_total", status="error")
        reset_correlation_id(token)
        raise
    reset_correlation_id(token)
    return {
        "correlation_id": cid,
        "status": state.get("monitoring", {}).get("status", "ok"),
        "answer": state.get("answer", ""),
        "intent": state.get("intent"),
        "plan": state.get("plan", []),
        "plan_reasoning": state.get("plan_reasoning", ""),
        "site_id": state.get("site_id"),
        "days": state.get("days"),
        "guardrail": state.get("guardrail"),
        "anomalies": state.get("anomalies", []),
        "recommendations": state.get("recommendations", []),
        "sources": [{k: v for k, v in s.items() if k != "text"} for s in state.get("sources", [])],
        "proposed_actions": state.get("proposed_actions", []),
        "analysis": (state.get("analysis") or {}).get("tool_results", {}),
        "trace": state.get("trace", []),
        "monitoring": state.get("monitoring", {}),
        "degraded": bool(state.get("degraded")),
        "llm": {"provider": gw.provider, "model": gw.model_name},
    }
