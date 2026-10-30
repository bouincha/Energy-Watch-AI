"""Request / response models of the public API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000, examples=["Any anomalies on my sites this month?"])
    site_id: str | None = Field(default=None, examples=["PLANT-KEN"])


class AskResponse(BaseModel):
    correlation_id: str
    status: str
    answer: str
    intent: str | None = None
    plan: list[str] = []
    plan_reasoning: str = ""
    site_id: str | None = None
    days: int | None = None
    guardrail: dict[str, Any] | None = None
    anomalies: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    proposed_actions: list[dict[str, Any]] = []
    analysis: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    monitoring: dict[str, Any] = {}
    degraded: bool = False
    llm: dict[str, Any] = {}


class DecisionRequest(BaseModel):
    actor: str = Field(default="energy-manager", min_length=2, max_length=60)
    comment: str | None = Field(default=None, max_length=500)


class ChaosRequest(BaseModel):
    llm_outage: bool | None = None
    llm_latency_ms: int | None = Field(default=None, ge=0, le=60000)
    tool_failure: bool | None = None


class ResolveRequest(BaseModel):
    actor: str = Field(default="on-call-engineer", min_length=2, max_length=60)
    note: str = Field(default="resolved manually", max_length=500)
