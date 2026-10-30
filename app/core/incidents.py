"""Incident management: detection hooks, runbook execution and timelines.

Runbook IDs here match the procedures documented in docs/RUNBOOK.md.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from app.core.observability import get_correlation_id, log_event, metrics, utcnow_iso

logger = logging.getLogger("energywatch.incidents")


@dataclass(frozen=True)
class Runbook:
    id: str
    title: str
    severity: str
    trigger: str
    steps: tuple[str, ...]


RUNBOOKS: dict[str, Runbook] = {
    "RB-001": Runbook(
        id="RB-001",
        title="LLM provider outage",
        severity="SEV-2",
        trigger="LLM circuit breaker opens after consecutive provider failures",
        steps=(
            "Detect: monitoring agent receives the circuit-breaker OPEN event",
            "Mitigate: agents switch to deterministic degraded mode (no LLM calls)",
            "Communicate: answers are flagged 'degraded' and the dashboard shows the incident",
            "Recover: breaker goes HALF-OPEN after the cool-down and probes the provider",
            "Resolve: first successful LLM call closes the breaker and the incident",
        ),
    ),
    "RB-002": Runbook(
        id="RB-002",
        title="Latency SLO breach",
        severity="SEV-3",
        trigger="An agent run exceeds the end-to-end latency SLO",
        steps=(
            "Detect: monitoring agent compares run duration with SLO_LATENCY_MS",
            "Diagnose: inspect the per-agent spans of the run (correlation ID) to find the slow agent",
            "Mitigate: check provider status, lower LLM_TIMEOUT_S or switch LLM_PROVIDER",
            "Resolve: incident closes automatically after 3 consecutive runs within the SLO",
        ),
    ),
    "RB-003": Runbook(
        id="RB-003",
        title="Prompt-injection attack burst",
        severity="SEV-3",
        trigger="3 or more blocked injection attempts within 10 minutes",
        steps=(
            "Detect: guardrail agent blocks the request and records a security event",
            "Contain: blocked requests never reach the LLM or the tools",
            "Investigate: review blocked queries in the run log (filter status=blocked)",
            "Resolve: an operator closes the incident after review",
        ),
    ),
    "RB-004": Runbook(
        id="RB-004",
        title="Data tool failure",
        severity="SEV-2",
        trigger="An energy data tool raises an error during a run",
        steps=(
            "Detect: specialist agent catches the tool exception and reports it in its span",
            "Mitigate: the run continues with the remaining agents, answer flagged as partial",
            "Diagnose: check the data source and the tool logs with the correlation ID",
            "Resolve: incident closes automatically on the next successful tool call",
        ),
    ),
}


@dataclass
class TimelineEntry:
    ts: str
    step: str
    detail: str
    correlation_id: str | None = None


@dataclass
class Incident:
    id: str
    runbook_id: str
    title: str
    severity: str
    status: str  # open | resolved
    detected_at: str
    resolved_at: str | None = None
    mttr_s: float | None = None
    affected_runs: list[str] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    _opened_mono: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("_opened_mono", None)
        data["runbook"] = asdict(RUNBOOKS[self.runbook_id])
        return data


class IncidentManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._incidents: list[Incident] = []
        self._seq = 0
        self._security_events: deque[float] = deque(maxlen=100)
        self._good_runs_since_latency_breach = 0

    # ---------------------------------------------------------------- core API
    def open_or_update(self, runbook_id: str, detail: str) -> Incident:
        rb = RUNBOOKS[runbook_id]
        cid = get_correlation_id()
        with self._lock:
            inc = self._open_for(runbook_id)
            if inc is None:
                self._seq += 1
                inc = Incident(
                    id=f"INC-{self._seq:04d}", runbook_id=rb.id, title=rb.title,
                    severity=rb.severity, status="open", detected_at=utcnow_iso(),
                    _opened_mono=time.monotonic(),
                )
                self._incidents.append(inc)
                inc.timeline.append(TimelineEntry(utcnow_iso(), rb.steps[0], detail, cid))
                inc.timeline.append(TimelineEntry(utcnow_iso(), rb.steps[1], "applied automatically", cid))
                created = True
            else:
                inc.timeline.append(TimelineEntry(utcnow_iso(), "Update", detail, cid))
                created = False
            if cid and cid not in inc.affected_runs:
                inc.affected_runs.append(cid)
        if created:
            metrics.inc("energywatch_incidents_total", runbook=runbook_id, severity=rb.severity)
            log_event(logger, f"incident {inc.id} opened: {rb.title}", logging.ERROR,
                      incident_id=inc.id, runbook=runbook_id, severity=rb.severity)
        return inc

    def resolve(self, runbook_id: str | None = None, incident_id: str | None = None,
                detail: str = "resolved", by: str = "monitoring-agent") -> Incident | None:
        with self._lock:
            inc = None
            if incident_id:
                inc = next((i for i in self._incidents if i.id == incident_id and i.status != "resolved"), None)
            elif runbook_id:
                inc = self._open_for(runbook_id)
            if inc is None:
                return None
            inc.status = "resolved"
            inc.resolved_at = utcnow_iso()
            inc.mttr_s = round(time.monotonic() - inc._opened_mono, 2)
            inc.timeline.append(TimelineEntry(utcnow_iso(), RUNBOOKS[inc.runbook_id].steps[-1],
                                              f"{detail} (by {by})", get_correlation_id()))
        log_event(logger, f"incident {inc.id} resolved", logging.WARNING,
                  incident_id=inc.id, mttr_s=inc.mttr_s, resolved_by=by)
        metrics.observe("energywatch_incident_mttr_seconds", inc.mttr_s or 0, runbook=inc.runbook_id)
        return inc

    def add_note(self, runbook_id: str, step: str, detail: str) -> None:
        with self._lock:
            inc = self._open_for(runbook_id)
            if inc:
                inc.timeline.append(TimelineEntry(utcnow_iso(), step, detail, get_correlation_id()))

    def list(self) -> list[Incident]:
        with self._lock:
            return list(reversed(self._incidents))

    def open_incidents(self) -> list[Incident]:
        with self._lock:
            return [i for i in self._incidents if i.status != "resolved"]

    def clear(self) -> None:
        with self._lock:
            self._incidents.clear()
            self._security_events.clear()
            self._good_runs_since_latency_breach = 0
            self._seq = 0

    def _open_for(self, runbook_id: str) -> Incident | None:
        return next((i for i in self._incidents if i.runbook_id == runbook_id and i.status != "resolved"), None)

    # ---------------------------------------------------------------- detectors
    def on_llm_breaker(self, old: str, new: str) -> None:
        if new == "open":
            self.open_or_update("RB-001", f"LLM circuit breaker transitioned {old} -> open")
        elif new == "closed":
            self.add_note("RB-001", "Recover", "half-open probe succeeded")
            self.resolve("RB-001", detail="LLM provider healthy again")

    def on_run_latency(self, duration_ms: float, slo_ms: float) -> None:
        if duration_ms > slo_ms:
            self._good_runs_since_latency_breach = 0
            self.open_or_update("RB-002", f"run took {duration_ms:.0f} ms (SLO {slo_ms:.0f} ms)")
        elif self._open_for("RB-002"):
            self._good_runs_since_latency_breach += 1
            if self._good_runs_since_latency_breach >= 3:
                self.resolve("RB-002", detail="3 consecutive runs within SLO")

    def on_security_event(self, reason: str) -> None:
        now = time.monotonic()
        self._security_events.append(now)
        recent = [t for t in self._security_events if now - t <= 600]
        if len(recent) >= 3:
            self.open_or_update("RB-003", f"{len(recent)} blocked attempts in 10 min (last: {reason})")

    def on_tool_result(self, tool: str, ok: bool, error: str | None = None) -> None:
        if not ok:
            self.open_or_update("RB-004", f"tool '{tool}' failed: {error}")
        elif self._open_for("RB-004"):
            self.resolve("RB-004", detail=f"tool '{tool}' succeeded")


incidents = IncidentManager()
