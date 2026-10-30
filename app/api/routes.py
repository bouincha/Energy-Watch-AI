"""REST API consumed by the dashboard and by external systems."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from app import __version__
from app.agents.graph import NODES, graph_mermaid, run_agent
from app.api.quality import quality_report
from app.api.schemas import AskRequest, AskResponse, ChaosRequest, DecisionRequest, ResolveRequest
from app.config import get_settings
from app.core.actions import actions
from app.core.incidents import RUNBOOKS, incidents
from app.core.observability import get_correlation_id, metrics, runs
from app.core.resilience import chaos
from app.core.security import RateLimiter
from app.data.energy_data import GRID_CO2_KG_PER_KWH, SITE_BY_ID, TARIFF_MAD_PER_KWH, get_dataset, live_reading
from app.governance import load_cards, validate_cards
from app.llm.gateway import get_gateway

router = APIRouter()
rate_limiter = RateLimiter(get_settings().rate_limit_per_minute)


def require_admin(x_api_key: str | None = Header(default=None)) -> None:
    """Write operations (approvals, chaos, incident closure) need the admin key when one is configured."""
    expected = get_settings().admin_api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")


def _site(site_id: str | None) -> str | None:
    if site_id in (None, "", "ALL", "all"):
        return None
    if site_id not in SITE_BY_ID:
        raise HTTPException(status_code=404, detail=f"Unknown site '{site_id}'")
    return site_id


# ------------------------------------------------------------------------------ health


@router.get("/health", tags=["ops"])
def health() -> dict:
    """Liveness probe (used by Docker HEALTHCHECK and Render)."""
    return {"status": "ok", "version": __version__}


@router.get("/ready", tags=["ops"])
def ready() -> dict:
    ds = get_dataset()
    gw = get_gateway()
    open_inc = incidents.open_incidents()
    return {
        "status": "degraded" if gw.breaker.state != "closed" or open_inc else "ready",
        "version": __version__, "environment": get_settings().environment,
        "data": {"days": ds.days, "meters": len(ds.series), "period_end": ds.end.isoformat()},
        "llm": gw.status(), "open_incidents": [i.id for i in open_inc], "chaos": chaos.state.to_dict(),
    }


@router.get("/metrics", tags=["ops"], response_class=PlainTextResponse)
def prometheus_metrics() -> str:
    """Prometheus exposition format."""
    return metrics.prometheus()


# ------------------------------------------------------------------------------ energy data


@router.get("/api/sites", tags=["energy"])
def list_sites() -> list[dict]:
    return get_dataset().sites()


@router.get("/api/overview", tags=["energy"])
def overview(site_id: str | None = None, days: int = Query(30, ge=1, le=30)) -> dict:
    site_id = _site(site_id)
    ds = get_dataset()
    return {
        "summary": ds.summary(site_id, days),
        "previous_week": ds.summary(site_id, 7),
        "daily_by_site": ds.daily(site_id, days, by="site"),
        "daily_by_category": ds.daily(site_id, days, by="category"),
        "hourly": ds.hourly(site_id, 72),
        "anomalies": [a.to_dict() for a in ds.anomalies(site_id)],
        "pue": ds.data_center_pue(14) if site_id in (None, "DC-RABAT") else None,
        "reference": {"tariff_mad_per_kwh": TARIFF_MAD_PER_KWH, "grid_co2_kg_per_kwh": GRID_CO2_KG_PER_KWH},
    }


@router.get("/api/anomalies", tags=["energy"])
def anomalies(site_id: str | None = None) -> list[dict]:
    return [a.to_dict() for a in get_dataset().anomalies(_site(site_id))]


@router.get("/api/meters/{meter_id}/profile", tags=["energy"])
def meter_profile(meter_id: str, days: int = Query(7, ge=1, le=30)) -> dict:
    ds = get_dataset()
    if meter_id not in ds.series:
        raise HTTPException(status_code=404, detail=f"Unknown meter '{meter_id}'")
    return ds.meter_profile(meter_id, days)


@router.get("/api/live", tags=["energy"])
def live(site_id: str | None = None) -> dict:
    return live_reading(get_dataset(), _site(site_id))


# ------------------------------------------------------------------------------ agents


@router.post("/api/agent/ask", tags=["agents"], response_model=AskResponse)
async def ask(body: AskRequest, request: Request) -> dict:
    """Run the multi-agent graph on a question. The response carries the correlation ID."""
    client = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(client):
        metrics.inc("energywatch_rate_limited_total")
        raise HTTPException(status_code=429, detail="Too many requests, slow down.")
    site_id = _site(body.site_id)
    return await run_in_threadpool(run_agent, body.query, site_id, get_correlation_id())


@router.get("/api/runs", tags=["monitoring"])
def list_runs(limit: int = Query(50, ge=1, le=500), status: str | None = None) -> list[dict]:
    items = [r.to_dict() for r in runs.list(500)]
    if status:
        items = [r for r in items if r["status"] == status]
    return items[:limit]


@router.get("/api/runs/{correlation_id}", tags=["monitoring"])
def get_run(correlation_id: str) -> dict:
    run = runs.get(correlation_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.to_dict()


@router.get("/api/monitoring/summary", tags=["monitoring"])
def monitoring_summary() -> dict:
    all_runs = runs.all()
    finished = [r for r in all_runs if r.status != "running"]
    durations = sorted(r.duration_ms for r in finished if r.status != "blocked")
    by_status: dict[str, int] = {}
    for r in finished:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    agent_stats: dict[str, dict] = {}
    for r in finished:
        for s in r.spans:
            st = agent_stats.setdefault(s.agent, {"executions": 0, "errors": 0, "degraded": 0, "total_ms": 0.0})
            st["executions"] += 1
            st["errors"] += s.status == "error"
            st["degraded"] += s.status == "degraded"
            st["total_ms"] += s.duration_ms
    for st in agent_stats.values():
        st["avg_ms"] = round(st.pop("total_ms") / max(st["executions"], 1), 1)

    def pct(p: float) -> float:
        return round(durations[min(len(durations) - 1, int(len(durations) * p))], 1) if durations else 0.0

    n = len(finished)
    errors = by_status.get("error", 0) + by_status.get("degraded", 0)
    s = get_settings()
    return {
        "total_runs": n, "by_status": by_status,
        "error_rate": round(errors / n, 3) if n else 0.0,
        "latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "max": durations[-1] if durations else 0.0},
        "slo": {"latency_ms": s.slo_latency_ms, "error_rate": s.slo_error_rate},
        "agents": agent_stats,
        "llm": get_gateway().status(),
        "llm_calls": {outcome: metrics.counter_value("energywatch_llm_calls_total", outcome=outcome)
                      for outcome in ("ok", "error", "short_circuited")},
        "guardrail_blocks": metrics.counter_value("energywatch_guardrail_blocks_total"),
        "open_incidents": len(incidents.open_incidents()),
        "chaos": chaos.state.to_dict(),
    }


# ------------------------------------------------------------------------------ human in the loop


@router.get("/api/actions", tags=["human-in-the-loop"])
def list_actions() -> list[dict]:
    return [a.to_dict() for a in actions.list()]


@router.post("/api/actions/{action_id}/approve", tags=["human-in-the-loop"], dependencies=[Depends(require_admin)])
def approve_action(action_id: str, body: DecisionRequest) -> dict:
    return _decide(action_id, True, body)


@router.post("/api/actions/{action_id}/reject", tags=["human-in-the-loop"], dependencies=[Depends(require_admin)])
def reject_action(action_id: str, body: DecisionRequest) -> dict:
    return _decide(action_id, False, body)


def _decide(action_id: str, approve: bool, body: DecisionRequest) -> dict:
    try:
        return actions.decide(action_id, approve, body.actor, body.comment).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Action not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/audit", tags=["human-in-the-loop"])
def audit_log(limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    return [e.to_dict() for e in actions.audit()[:limit]]


# ------------------------------------------------------------------------------ incidents & chaos


@router.get("/api/incidents", tags=["incidents"])
def list_incidents() -> list[dict]:
    return [i.to_dict() for i in incidents.list()]


@router.get("/api/runbooks", tags=["incidents"])
def list_runbooks() -> list[dict]:
    return [{"id": r.id, "title": r.title, "severity": r.severity, "trigger": r.trigger, "steps": list(r.steps)}
            for r in RUNBOOKS.values()]


@router.post("/api/incidents/{incident_id}/resolve", tags=["incidents"], dependencies=[Depends(require_admin)])
def resolve_incident(incident_id: str, body: ResolveRequest) -> dict:
    inc = incidents.resolve(incident_id=incident_id, detail=body.note, by=body.actor)
    if inc is None:
        raise HTTPException(status_code=404, detail="No open incident with this ID")
    actions.record(body.actor, "incident.resolved", incident_id, body.note)
    return inc.to_dict()


@router.get("/api/chaos", tags=["incidents"])
def get_chaos() -> dict:
    return chaos.state.to_dict()


@router.post("/api/chaos", tags=["incidents"], dependencies=[Depends(require_admin)])
def set_chaos(body: ChaosRequest) -> dict:
    """Inject failures to rehearse the incident runbook (game day)."""
    state = chaos.update(**body.model_dump())
    actions.record("operator", "chaos.updated", "chaos", str(state.to_dict()))
    return state.to_dict()


# ------------------------------------------------------------------------------ quality


@router.get("/api/quality", tags=["quality"])
async def quality(refresh: bool = False) -> dict:
    """CI status (GitHub Actions), live evaluation gates and runtime self-checks."""
    return await run_in_threadpool(quality_report, refresh)


# ------------------------------------------------------------------------------ governance


@router.get("/api/agent-card", tags=["governance"])
def agent_card() -> dict:
    cards = load_cards()
    return {**cards, "graph_mermaid": graph_mermaid(),
            "validation": {"problems": validate_cards(cards, list(NODES), __version__)}}


@router.get("/api/agent-card/{agent_id}", tags=["governance"])
def agent_card_one(agent_id: str) -> dict:
    for card in load_cards()["agents"]:
        if card["id"] == agent_id:
            return card
    raise HTTPException(status_code=404, detail="Unknown agent")
