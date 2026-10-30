"""The agents (LangGraph nodes) of EnergyWatch AI.

guardrails -> supervisor -> [consumption_analyst | anomaly_detector | energy_advisor]* -> report_writer -> monitor

Every node is wrapped by ``traced`` which records a span (duration, status, tool calls) on the
run identified by the correlation ID. Each agent that uses the LLM has a deterministic fallback:
if the model is unavailable the agent still answers and flags the run as *degraded*.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from app.agents import prompts
from app.agents.state import SPECIALISTS, EnergyState, RoutingDecision
from app.agents.tools import ANALYST_TOOLS, run_tool
from app.config import get_settings
from app.core.actions import actions
from app.core.incidents import incidents
from app.core.observability import (
    Span,
    get_correlation_id,
    log_event,
    metrics,
    reset_correlation_id,
    runs,
    set_correlation_id,
    utcnow_iso,
)
from app.core.security import check_input, check_output
from app.data.energy_data import SITE_BY_ID
from app.llm.gateway import LLMUnavailable, get_gateway
from app.rag.retriever import get_retriever

logger = logging.getLogger("energywatch.agents")

NodeFn = Callable[[EnergyState], dict[str, Any]]


# =============================================================================== tracing


def traced(agent: str) -> Callable[[NodeFn], NodeFn]:
    """Monitoring wrapper: one span per agent execution, linked to the correlation ID."""

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        def wrapper(state: EnergyState) -> dict[str, Any]:
            cid = state.get("correlation_id")
            token = set_correlation_id(cid) if cid and get_correlation_id() != cid else None
            started = utcnow_iso()
            t0 = time.perf_counter()
            try:
                update = dict(fn(state) or {})
                meta = update.pop("_span", {})
                status = meta.get("status", "ok")
            except Exception as exc:  # an agent crash must never crash the whole run
                logger.exception("agent %s failed", agent)
                update = {"errors": [f"{agent}: {type(exc).__name__}: {exc}"]}
                meta = {"summary": "agent failed", "error": str(exc)}
                status = "error"
            duration = (time.perf_counter() - t0) * 1000
            span = Span(agent=agent, started_at=started, duration_ms=round(duration, 1), status=status,
                        summary=meta.get("summary", ""), tool_calls=meta.get("tool_calls", []),
                        error=meta.get("error"))
            runs.add_span(cid, span)
            metrics.observe("energywatch_agent_latency_ms", duration, agent=agent)
            metrics.inc("energywatch_agent_executions_total", agent=agent, status=status)
            log_event(logger, f"agent {agent} finished: {status}", agent=agent, status=status,
                      duration_ms=round(duration, 1), summary=span.summary)
            update["completed"] = [agent]
            update["trace"] = [asdict(span)]
            if status in ("degraded", "error"):
                update["degraded"] = True
            if token is not None:
                reset_correlation_id(token)
            return update

        wrapper.agent_name = agent  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _fmt(x: float, digits: int = 0) -> str:
    return f"{x:,.{digits}f}"


# =============================================================================== routing rules

SITE_KEYWORDS = {
    "HQ-CASA": ("hq", "headquarter", "casablanca", "casa", "office"),
    "PLANT-KEN": ("plant", "factory", "kenitra", "manufactur", "assembly", "production"),
    "DC-RABAT": ("data center", "datacenter", "data centre", "rabat", "server", "pue"),
    "CLINIC-MRK": ("clinic", "hospital", "marrakech", "healthcare"),
}

INTENT_KEYWORDS = {
    "report": ("report", "summary of everything", "executive", "full overview", "monthly review",
               "all sites overview", "overall status", "big picture"),
    "anomaly": ("anomal", "spike", "unusual", "abnormal", "strange", "alert", "leak", "waste", "wasting",
                "wrong", "problem", "issue", "drift", "why is", "why was", "why did", "suspicious",
                "night", "weekend", "unexpected"),
    "advice": ("how can", "how do", "how to", "recommend", "advice", "advise", "tip", "should we", "should i",
               "reduce", "save", "saving", "improve", "optimi", "lower", "cut", "best practice"),
}

DEFAULT_PLANS = {
    "consumption": ["consumption_analyst"],
    "anomaly": ["anomaly_detector", "energy_advisor"],
    "advice": ["consumption_analyst", "energy_advisor"],
    "report": ["consumption_analyst", "anomaly_detector", "energy_advisor"],
}
DEFAULT_DAYS = {"consumption": 7, "anomaly": 30, "advice": 30, "report": 30}


def detect_site(text: str) -> str | None:
    t = text.lower()
    for site_id, words in SITE_KEYWORDS.items():
        if site_id.lower() in t or any(w in t for w in words):
            return site_id
    return None


def detect_days(text: str) -> int | None:
    t = text.lower()
    m = re.search(r"(?:last|past)\s+(\d{1,2})\s+days?", t)
    if m:
        return max(1, min(int(m.group(1)), 30))
    if "yesterday" in t or "today" in t or "last 24" in t:
        return 1
    if "week" in t:
        return 7
    if "month" in t or "30 days" in t:
        return 30
    return None


def rule_route(query: str, site_hint: str | None = None) -> RoutingDecision:
    """Deterministic planner used in mock mode and as the fallback when the LLM is down."""
    t = query.lower()
    intent = "consumption"
    for name in ("report", "anomaly", "advice"):
        if any(k in t for k in INTENT_KEYWORDS[name]):
            intent = name
            break
    days = detect_days(query) or DEFAULT_DAYS[intent]
    site = detect_site(query) or site_hint
    return RoutingDecision(intent=intent, agents=list(DEFAULT_PLANS[intent]), site_id=site, days=days,
                           reasoning=f"Keyword-based plan for a '{intent}' question.")


def _sanitize_decision(d: RoutingDecision, site_hint: str | None, query: str) -> RoutingDecision:
    agents = [a for a in dict.fromkeys(d.agents) if a in SPECIALISTS] or DEFAULT_PLANS[d.intent]
    agents.sort(key=SPECIALISTS.index)  # analysts first, advisor last
    site = d.site_id if d.site_id in SITE_BY_ID else None
    if site is None:
        site = detect_site(query) or site_hint
    return d.model_copy(update={"agents": agents, "site_id": site, "days": max(1, min(d.days, 30))})


# =============================================================================== nodes


@traced("guardrails")
def guardrails_node(state: EnergyState) -> dict[str, Any]:
    result = check_input(state.get("query", ""), get_settings().max_query_chars)
    if result.allowed:
        return {"guardrail": result.to_dict(), "query": result.sanitized,
                "_span": {"summary": "input accepted"}}
    metrics.inc("energywatch_guardrail_blocks_total", category=result.category)
    if result.category == "injection":
        incidents.on_security_event(result.reason)
        actions.record("guardrails", "security.blocked", get_correlation_id() or "-", result.reason)
    return {"guardrail": result.to_dict(), "answer": result.reason,
            "_span": {"status": "blocked", "summary": f"blocked: {result.category}"}}


@traced("supervisor")
def supervisor_node(state: EnergyState) -> dict[str, Any]:
    query = state["query"]
    site_hint = state.get("site_id")
    fallback = rule_route(query, site_hint)
    gw = get_gateway()
    status = "ok"
    try:
        decision = gw.structured(
            "supervisor", RoutingDecision, prompts.SUPERVISOR,
            f"Question: {query}\nSite selected in the dashboard: {site_hint or 'all sites'}",
            mock=lambda: fallback,
        )
    except LLMUnavailable:
        decision, status = fallback, "degraded"
    except Exception as exc:  # malformed structured output -> safe fallback
        log_event(logger, f"invalid routing output, using rules: {exc}", logging.WARNING)
        decision, status = fallback, "degraded"
    decision = _sanitize_decision(decision, site_hint, query)
    return {
        "intent": decision.intent, "plan": decision.agents, "site_id": decision.site_id,
        "days": decision.days, "plan_reasoning": decision.reasoning,
        "_span": {"status": status,
                  "summary": f"intent={decision.intent} plan={'>'.join(decision.agents)} "
                             f"site={decision.site_id or 'ALL'} days={decision.days}"},
    }


def _rule_tool_calls(query: str, site: str | None, days: int) -> list[dict]:
    t = query.lower()
    calls = [{"name": "get_consumption_summary", "args": {"site_id": site, "days": days}}]
    if any(k in t for k in ("trend", "compare", "increase", "decrease", "previous", "change", "evolution",
                            "higher", "lower", "report")):
        calls.append({"name": "compare_periods", "args": {"site_id": site, "days": days}})
    if any(k in t for k in ("top", "biggest", "largest", "most", "breakdown", "which equipment", "where",
                            "consumer", "report", "reduce", "save")):
        calls.append({"name": "get_top_consumers", "args": {"site_id": site, "days": days, "n": 5}})
    if site == "DC-RABAT" or "pue" in t or "data center" in t:
        calls.append({"name": "get_data_center_pue", "args": {"days": min(days, 14)}})
    return calls


def _consumption_text(results: dict[str, Any]) -> str:
    lines = []
    s = results.get("get_consumption_summary")
    if s:
        where = SITE_BY_ID[s["site_id"]].name if s["site_id"] in SITE_BY_ID else "All sites"
        lines.append(f"- **{where}** used **{_fmt(s['consumption_kwh'])} kWh** over the last {s['days']} days "
                     f"(net cost **{_fmt(s['cost_mad'])} MAD**, {s['co2_tonnes']} t CO2).")
        lines.append(f"- Peak demand: {_fmt(s['peak_demand_kw'])} kW on {s['peak_at'][:16].replace('T', ' ')} UTC.")
        if s["by_category"]:
            total = s["consumption_kwh"] or 1
            top = list(s["by_category"].items())[:3]
            lines.append("- Main usages: " + ", ".join(f"{k} {v / total * 100:.0f}%" for k, v in top) + ".")
        if s["solar_generation_kwh"]:
            lines.append(f"- Solar produced {_fmt(s['solar_generation_kwh'])} kWh "
                         f"({s['renewable_share_pct']}% of consumption).")
        peak_share = s["by_tariff_period"].get("peak", 0) / (s["consumption_kwh"] or 1) * 100
        lines.append(f"- {peak_share:.0f}% of the energy was consumed in the expensive peak period (18:00-22:00).")
    c = results.get("compare_periods")
    if c and c.get("total_change_pct") is not None:
        movers = [f"{k} {v['change_pct']:+.0f}%" for k, v in c["by_category"].items()
                  if v["change_pct"] is not None and abs(v["change_pct"]) >= 8][:3]
        lines.append(f"- Versus the previous {c['days']} days: {c['total_change_pct']:+.1f}%"
                     + (f" (biggest changes: {', '.join(movers)})." if movers else "."))
    top = results.get("get_top_consumers")
    if top:
        lines.append("- Top consumers: " + ", ".join(f"{x['meter_id']} ({x['share_pct']}%)" for x in top[:3]) + ".")
    p = results.get("get_data_center_pue")
    if p:
        trend = "rising" if p["current_pue"] > p["baseline_pue"] + 0.03 else "stable"
        lines.append(f"- Data center PUE is **{p['current_pue']}** ({trend}, target {p['target_pue']}).")
    return "\n".join(lines) or "No consumption data available."


@traced("consumption_analyst")
def consumption_analyst_node(state: EnergyState) -> dict[str, Any]:
    query, site, days = state["query"], state.get("site_id"), state.get("days", 7)
    gw = get_gateway()
    status = "ok"
    rule_calls = _rule_tool_calls(query, site, days)
    try:
        calls = gw.choose_tools(
            "consumption_analyst", prompts.CONSUMPTION_ANALYST,
            f"Question: {query}\nsite_id: {site or 'null (all sites)'}\ndays: {days}",
            ANALYST_TOOLS, mock=lambda: rule_calls,
        ) or rule_calls
    except LLMUnavailable:
        calls, status = rule_calls, "degraded"
    results: dict[str, Any] = {}
    errors: list[str] = []
    for call in calls[:4]:
        try:
            results[call["name"]] = run_tool(call["name"], call.get("args") or {})
        except Exception as exc:
            errors.append(f"consumption_analyst/{call['name']}: {exc}")
    if not results:
        return {"analysis": {"error": "data tools unavailable"}, "errors": errors,
                "_span": {"status": "error", "summary": "all tools failed",
                          "tool_calls": [c["name"] for c in calls], "error": "; ".join(errors)}}
    fallback_text = _consumption_text(results)
    try:
        text = gw.write("consumption_analyst", prompts.CONSUMPTION_WRITER,
                        f"Question: {query}\nTool results:\n{json.dumps(results, default=str)[:6000]}",
                        mock=lambda: fallback_text)
    except LLMUnavailable:
        text, status = fallback_text, "degraded"
    if errors:
        status = "degraded"
    return {"analysis": {"tool_results": results, "narrative": text}, "errors": errors,
            "_span": {"status": status, "summary": f"{len(results)} tool(s) executed",
                      "tool_calls": list(results)}}


ROOT_CAUSES = {
    ("hvac", "night_operation"): "BMS schedule override left active or a missing holiday calendar",
    ("hvac", "weekend_operation"): "HVAC schedule not following the working calendar",
    ("hvac", "spike"): "chiller fault (short cycling, low refrigerant or failed condenser fan)",
    ("compressed_air", "sustained_increase"): "compressed-air leak (consumption also high when production is stopped)",
    ("cooling", "drift"): "progressive cooling degradation: fouled filters/coils or a setpoint drift",
    ("lighting", "weekend_operation"): "lighting schedule missing or overridden for the weekend",
    ("lighting", "night_operation"): "lighting schedule missing or overridden at night",
}


def _anomaly_text(anoms: list[dict]) -> str:
    if not anoms:
        return "No significant anomaly detected in the selected period. Consumption follows the baseline."
    total = sum(a["excess_cost_mad"] for a in anoms)
    lines = [f"Detected **{len(anoms)} anomal{'y' if len(anoms) == 1 else 'ies'}** with "
             f"**{_fmt(total)} MAD** of avoidable cost:"]
    for a in anoms[:6]:
        cause = ROOT_CAUSES.get((a["category"], a["pattern"]), "abnormal operation - inspection needed")
        lines.append(f"- **{a['id']} · {a['site_name']} - {a['meter_name']}** ({a['severity']}): {a['description']} "
                     f"Excess {_fmt(a['excess_kwh'])} kWh ≈ {_fmt(a['excess_cost_mad'])} MAD. Likely cause: {cause}.")
    return "\n".join(lines)


@traced("anomaly_detector")
def anomaly_detector_node(state: EnergyState) -> dict[str, Any]:
    site, days = state.get("site_id"), state.get("days", 30)
    try:
        anoms = run_tool("detect_anomalies", {"site_id": site, "days": days})
    except Exception as exc:
        return {"anomalies": [], "anomaly_summary": "Anomaly detection is temporarily unavailable.",
                "errors": [f"anomaly_detector: {exc}"],
                "_span": {"status": "error", "summary": "detection failed", "tool_calls": ["detect_anomalies"],
                          "error": str(exc)}}
    fallback_text = _anomaly_text(anoms)
    status = "ok"
    try:
        text = fallback_text if not anoms else get_gateway().write(
            "anomaly_detector", prompts.ANOMALY_WRITER,
            f"Question: {state['query']}\nDetected anomalies (JSON):\n{json.dumps(anoms)[:6000]}\n"
            f"Known root-cause patterns: {json.dumps({f'{k[0]}/{k[1]}': v for k, v in ROOT_CAUSES.items()})}",
            mock=lambda: fallback_text)
    except LLMUnavailable:
        text, status = fallback_text, "degraded"
    return {"anomalies": anoms, "anomaly_summary": text,
            "_span": {"status": status, "summary": f"{len(anoms)} anomalies found",
                      "tool_calls": ["detect_anomalies"]}}


PLAYBOOK: dict[tuple[str, str], dict[str, Any]] = {
    ("compressed_air", "sustained_increase"): {
        "title": "Run an ultrasonic leak survey and repair compressed-air leaks", "risk": "medium",
        "recovery": 0.85, "ongoing_rate": True, "query": "compressed air leaks leak load survey"},
    ("cooling", "drift"): {
        "title": "Inspect CRAH filters/coils and verify chilled-water setpoints", "risk": "high",
        "recovery": 0.8, "ongoing_rate": True, "query": "PUE drift data center cooling filters setpoint"},
    ("lighting", "weekend_operation"): {
        "title": "Program a lighting sweep-off for nights and weekends", "risk": "low",
        "recurrence": 4, "query": "lighting left on outside working hours weekend schedule"},
    ("hvac", "night_operation"): {
        "title": "Remove the BMS override and enable automatic override expiry (4h)", "risk": "low",
        "recurrence": 2, "query": "HVAC scheduling occupancy night override BMS"},
    ("hvac", "spike"): {
        "title": "Inspect the chiller for short cycling (clinical area: facility manager approval)",
        "risk": "high", "recurrence": 2, "query": "chiller faults short cycling HVAC spike healthcare"},
}

GENERAL_TIPS = {
    "process": "production idle load shutdown outside production",
    "hvac": "HVAC setpoints deadband scheduling",
    "lighting": "LED retrofit lighting controls occupancy sensors",
    "compressed_air": "compressed air pressure optimization shutdown",
    "cooling": "data center airflow management containment",
    "medical": "healthcare standby imaging equipment",
}


def _monthly_savings(a: dict, play: dict) -> tuple[float, float]:
    price = a["excess_cost_mad"] / a["excess_kwh"] if a["excess_kwh"] else 1.0
    if play.get("ongoing_rate"):
        kwh = a["excess_kwh"] / max(a["hours"], 1) * 24 * 30 * play["recovery"]
    else:
        kwh = a["excess_kwh"] * play.get("recurrence", 1)
    return kwh, kwh * price


@traced("energy_advisor")
def energy_advisor_node(state: EnergyState) -> dict[str, Any]:
    query = state["query"]
    anoms = state.get("anomalies")
    tool_calls = ["knowledge_base.search"]
    if anoms is None:
        # Advice without a prior anomaly pass: the advisor checks the site's anomalies itself,
        # so its recommendations target real problems instead of generic tips.
        try:
            anoms = run_tool("detect_anomalies", {"site_id": state.get("site_id"), "days": state.get("days", 30)})
            tool_calls.insert(0, "detect_anomalies")
        except Exception:
            anoms = []
    retriever = get_retriever()

    queries: list[tuple[str, dict | None]] = []
    for a in anoms[:5]:
        play = PLAYBOOK.get((a["category"], a["pattern"]))
        queries.append((play["query"] if play else f"{a['category']} {a['pattern']}", a))
    if state.get("intent") == "advice" or not anoms:
        queries.append((query, None))
        top = (state.get("analysis") or {}).get("tool_results", {}).get("get_consumption_summary", {})
        for cat in list(top.get("by_category", {}))[:2]:
            if cat in GENERAL_TIPS:
                queries.append((GENERAL_TIPS[cat], None))

    sources: dict[str, dict] = {}
    recs: list[dict] = []
    proposed: list[dict] = []
    seen_sections: set[str] = set()
    for q, a in queries:
        hits = retriever.search(q, k=2)
        for h in hits:
            sources.setdefault(h["id"], h)
        cite = hits[0]["citation"] if hits else None
        if a is None:
            fresh = [h for h in hits if h["citation"] not in seen_sections]
            if fresh:
                h = fresh[0]
                seen_sections.add(h["citation"])
                first_sentence = re.split(r"(?<=\.)\s", h["text"])[0]
                recs.append({"title": h["section"], "detail": first_sentence, "source": h["citation"],
                             "risk": "low", "savings_kwh_month": None, "savings_mad_month": None})
            continue
        if cite:
            seen_sections.add(cite)
        play = PLAYBOOK.get((a["category"], a["pattern"]),
                            {"title": f"Investigate {a['meter_name']} at {a['site_name']}", "risk": "medium",
                             "recurrence": 1})
        kwh, mad = _monthly_savings(a, play)
        rec = {"title": play["title"], "detail": a["description"], "source": cite, "risk": play["risk"],
               "anomaly_id": a["id"], "site_id": a["site_id"],
               "savings_kwh_month": round(kwh), "savings_mad_month": round(mad)}
        recs.append(rec)
        act = actions.propose(title=play["title"], site_id=a["site_id"], equipment=a["meter_id"],
                              risk=play["risk"], savings_kwh=kwh, savings_mad=mad,
                              rationale=f"{a['id']}: {a['description']}", proposed_by="energy_advisor")
        proposed.append(act.to_dict())

    def fallback() -> str:
        if not recs:
            return "No specific recommendation found in the knowledge base for this question."
        lines = ["Prioritized recommendations:"]
        for i, r in enumerate(recs[:6], 1):
            saving = (f" - est. savings **{_fmt(r['savings_kwh_month'])} kWh/month "
                      f"(≈{_fmt(r['savings_mad_month'])} MAD)**" if r["savings_kwh_month"] else f": {r['detail']}")
            src = f" [{r['source']}]" if r["source"] else ""
            lines.append(f"{i}. **{r['title']}**{saving.rstrip('.')}.{src}")
        return "\n".join(lines)

    fallback_text = fallback()
    status = "ok"
    context = "\n\n".join(f"[{s['citation']}]\n{s['text']}" for s in list(sources.values())[:6])
    try:
        text = get_gateway().write(
            "energy_advisor", prompts.ADVISOR,
            f"Question: {query}\nFacts (recommendations with computed savings):\n{json.dumps(recs)}\n\n"
            f"Knowledge-base excerpts:\n{context}", mock=lambda: fallback_text)
    except LLMUnavailable:
        text, status = fallback_text, "degraded"
    return {"recommendations": recs, "advice_summary": text, "sources": list(sources.values()),
            "proposed_actions": proposed, "anomalies": anoms,
            "_span": {"status": status,
                      "summary": f"{len(recs)} recommendations, {len(sources)} sources, {len(proposed)} actions",
                      "tool_calls": tool_calls}}


def _headline(state: EnergyState) -> str:
    intent = state.get("intent")
    site = state.get("site_id")
    where = SITE_BY_ID[site].name if site in SITE_BY_ID else "All sites"
    anoms = state.get("anomalies") or []
    if intent in ("anomaly", "report") and anoms:
        return (f"{where}: {len(anoms)} anomal{'y' if len(anoms) == 1 else 'ies'} detected - "
                f"{_fmt(sum(a['excess_cost_mad'] for a in anoms))} MAD of avoidable cost")
    s = (state.get("analysis") or {}).get("tool_results", {}).get("get_consumption_summary")
    if s:
        return f"{where}: {_fmt(s['consumption_kwh'])} kWh and {_fmt(s['cost_mad'])} MAD over the last {s['days']} days"
    if intent == "anomaly":
        return f"{where}: no significant anomaly detected"
    return f"{where}: energy assessment"


@traced("report_writer")
def report_writer_node(state: EnergyState) -> dict[str, Any]:
    sections: list[tuple[str, str]] = []
    analysis = state.get("analysis") or {}
    if analysis.get("narrative"):
        sections.append(("Consumption", analysis["narrative"]))
    if "anomaly_detector" in (state.get("plan") or []):
        sections.append(("Anomalies", state.get("anomaly_summary") or "Anomaly detection unavailable."))
    recs = state.get("recommendations")
    if state.get("advice_summary") and "energy_advisor" in (state.get("plan") or []):
        sections.append(("Recommendations", state["advice_summary"]))
    elif recs is not None and "energy_advisor" in (state.get("plan") or []):
        lines = []
        for i, r in enumerate(recs[:6], 1):
            saving = (f" - est. **{_fmt(r['savings_mad_month'])} MAD/month**" if r.get("savings_mad_month")
                      else f": {r['detail']}")
            src = f" [{r['source']}]" if r.get("source") else ""
            lines.append(f"{i}. **{r['title']}**{saving.rstrip('.')}.{src}")
        sections.append(("Recommendations", "\n".join(lines) or "No recommendation."))
    pending = [a for a in (state.get("proposed_actions") or []) if a["status"] == "pending_approval"]
    if pending:
        sections.append(("Actions awaiting human approval",
                         "\n".join(f"- `{a['id']}` {a['title']} (risk: {a['risk']})" for a in pending)))

    headline = _headline(state)
    fallback_text = f"**{headline}**\n\n" + "\n\n".join(f"### {t}\n{b}" for t, b in sections)
    status = "ok"
    try:
        text = get_gateway().write(
            "report_writer", prompts.REPORT_WRITER,
            f"User question: {state['query']}\nHeadline: {headline}\n\nSpecialist outputs:\n"
            + "\n\n".join(f"## {t}\n{b}" for t, b in sections),
            mock=lambda: fallback_text)
    except LLMUnavailable:
        text, status = fallback_text, "degraded"
    if state.get("degraded") or status == "degraded":
        text += ("\n\n> ⚠️ **Degraded mode** - part of this answer was produced by deterministic fallbacks "
                 "because the AI model or a data tool was unavailable (runbook RB-001/RB-004).")
    return {"answer": check_output(text),
            "_span": {"status": status, "summary": f"{len(sections)} section(s), {len(text)} chars"}}


@traced("monitor")
def monitor_node(state: EnergyState) -> dict[str, Any]:
    """Monitoring agent: evaluates the run against SLOs, updates the run record and raises incidents."""
    settings = get_settings()
    cid = state.get("correlation_id")
    duration = (time.perf_counter() - state.get("started_perf", time.perf_counter())) * 1000
    blocked = not (state.get("guardrail") or {}).get("allowed", False)
    trace = state.get("trace") or []
    errors = state.get("errors") or []
    degraded = bool(state.get("degraded"))
    if blocked:
        status = "blocked"
    elif not state.get("answer"):
        status = "error"
    elif degraded or errors:
        status = "degraded"
    else:
        status = "ok"
    slo_ok = duration <= settings.slo_latency_ms
    if not blocked:
        incidents.on_run_latency(duration, settings.slo_latency_ms)
    run = runs.get(cid) if cid else None
    health = {
        "status": status, "duration_ms": round(duration, 1), "slo_latency_ms": settings.slo_latency_ms,
        "slo_ok": slo_ok, "agents_run": [s["agent"] for s in trace], "errors": errors,
        "llm_provider": get_gateway().provider, "llm_calls": run.llm_calls if run else 0,
        "llm_failures": run.llm_failures if run else 0, "circuit_breaker": get_gateway().breaker.state,
        "open_incidents": [i.id for i in incidents.open_incidents()],
    }
    if run:
        run.status = status
        run.intent = state.get("intent")
        run.plan = state.get("plan") or []
        run.duration_ms = round(duration, 1)
        run.health = health
        run.answer_preview = (state.get("answer") or "")[:200]
    metrics.inc("energywatch_runs_total", status=status)
    metrics.observe("energywatch_run_latency_ms", duration)
    log_event(logger, f"run finished: {status}", status=status, duration_ms=round(duration, 1),
              slo_ok=slo_ok, intent=state.get("intent"))
    return {"monitoring": health, "_span": {"status": "ok", "summary": f"run {status}, slo_ok={slo_ok}"}}


def next_step(state: EnergyState) -> str:
    """Dispatcher: run the planned specialists in order, then the report writer."""
    done = set(state.get("completed") or [])
    for agent in state.get("plan") or []:
        if agent not in done:
            return agent
    return "report_writer"


def after_guardrails(state: EnergyState) -> str:
    return "supervisor" if (state.get("guardrail") or {}).get("allowed") else "monitor"
