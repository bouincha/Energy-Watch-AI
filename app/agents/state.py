"""Shared LangGraph state passed between the agents."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

SPECIALISTS = ("consumption_analyst", "anomaly_detector", "energy_advisor")
Intent = Literal["consumption", "anomaly", "advice", "report"]
SiteId = Literal["HQ-CASA", "PLANT-KEN", "DC-RABAT", "CLINIC-MRK"]


class RoutingDecision(BaseModel):
    """Structured output of the supervisor (planner)."""

    intent: Intent = Field(description="consumption = usage/cost questions, anomaly = unusual consumption, "
                                       "advice = how to save energy, report = full multi-site report")
    agents: list[Literal["consumption_analyst", "anomaly_detector", "energy_advisor"]] = Field(
        description="Ordered list of specialist agents to run")
    site_id: SiteId | None = Field(default=None, description="Site the question is about, null for all sites")
    days: int = Field(default=7, ge=1, le=30, description="Analysis window in days")
    reasoning: str = Field(default="", description="One sentence explaining the plan")


class EnergyState(TypedDict, total=False):
    # input
    correlation_id: str
    query: str
    site_id: str | None
    started_perf: float
    # guardrails & planning
    guardrail: dict[str, Any]
    intent: str
    plan: list[str]
    plan_reasoning: str
    days: int
    # specialist outputs
    analysis: dict[str, Any]
    anomalies: list[dict[str, Any]]
    anomaly_summary: str
    recommendations: list[dict[str, Any]]
    advice_summary: str
    sources: list[dict[str, Any]]
    proposed_actions: list[dict[str, Any]]
    # final
    answer: str
    monitoring: dict[str, Any]
    # accumulated across nodes
    completed: Annotated[list[str], operator.add]
    trace: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
    degraded: Annotated[bool, operator.or_]
