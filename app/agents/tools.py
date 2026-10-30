"""Read-only tools exposed to the agents (least privilege: no tool can modify equipment)."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.core.incidents import incidents
from app.core.resilience import chaos
from app.data.energy_data import SITE_BY_ID, get_dataset


class ToolInputError(ValueError):
    pass


def _validate(site_id: str | None, days: int) -> tuple[str | None, int]:
    if site_id in ("", "ALL", "all", "null", "None"):
        site_id = None
    if site_id is not None and site_id not in SITE_BY_ID:
        raise ToolInputError(f"Unknown site_id '{site_id}'. Valid: {', '.join(SITE_BY_ID)}")
    try:
        days = int(days)
    except (TypeError, ValueError) as exc:
        raise ToolInputError("days must be an integer") from exc
    return site_id, max(1, min(days, 30))


@tool
def get_consumption_summary(site_id: str | None = None, days: int = 7) -> dict:
    """Total consumption (kWh), cost (MAD), CO2, peak demand, solar generation and breakdown by usage
    for one site (HQ-CASA, PLANT-KEN, DC-RABAT, CLINIC-MRK) or all sites (site_id=null) over N days."""
    site_id, days = _validate(site_id, days)
    s = get_dataset().summary(site_id, days)
    s.pop("by_meter", None)
    return s


@tool
def compare_periods(site_id: str | None = None, days: int = 7) -> dict:
    """Compare the last N days with the N days before, per usage category (change in %)."""
    site_id, days = _validate(site_id, days)
    return get_dataset().compare_periods(site_id, days)


@tool
def get_top_consumers(site_id: str | None = None, days: int = 7, n: int = 5) -> list[dict]:
    """The N meters (equipment) that consumed the most energy over the last N days."""
    site_id, days = _validate(site_id, days)
    by_meter = get_dataset().summary(site_id, days)["by_meter"]
    total = sum(by_meter.values()) or 1
    return [{"meter_id": k, "kwh": v, "share_pct": round(v / total * 100, 1)}
            for k, v in list(by_meter.items())[: max(1, min(int(n), 10))]]


@tool
def get_data_center_pue(days: int = 7) -> dict:
    """Daily Power Usage Effectiveness (PUE) of the Rabat data center over the last N days."""
    _, days = _validate(None, days)
    return get_dataset().data_center_pue(days)


@tool
def detect_anomalies(site_id: str | None = None, days: int = 30) -> list[dict]:
    """Statistical anomaly detection (robust z-score vs. a per-hour baseline) for one or all sites."""
    site_id, days = _validate(site_id, days)
    ds = get_dataset()
    cutoff = ds.ts(ds.hours - days * 24).isoformat()
    return [a.to_dict() for a in ds.anomalies(site_id) if a.end > cutoff]


ANALYST_TOOLS = [get_consumption_summary, compare_periods, get_top_consumers, get_data_center_pue]
TOOLS_BY_NAME = {t.name: t for t in [*ANALYST_TOOLS, detect_anomalies]}


def run_tool(name: str, args: dict[str, Any]) -> Any:
    """Execute a tool by name with validation, chaos hooks and incident reporting."""
    if name not in TOOLS_BY_NAME:
        raise ToolInputError(f"Unknown tool '{name}'")
    try:
        chaos.before_tool_call(name)
        result = TOOLS_BY_NAME[name].invoke(args)
    except ToolInputError:
        raise
    except Exception as exc:
        incidents.on_tool_result(name, ok=False, error=str(exc))
        raise
    incidents.on_tool_result(name, ok=True)
    return result
