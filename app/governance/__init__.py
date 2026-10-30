"""Agent governance: machine-readable agent cards (see docs/AGENT_CARD.md)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

CARDS_PATH = Path(__file__).parent / "agent_cards.json"

REQUIRED_AGENT_FIELDS = (
    "id", "name", "version", "role", "description", "uses_llm", "tools", "inputs", "outputs",
    "permissions", "autonomy", "human_oversight", "failure_modes", "fallback", "metrics", "limitations",
)


@lru_cache
def load_cards() -> dict:
    return json.loads(CARDS_PATH.read_text(encoding="utf-8"))


def validate_cards(cards: dict, graph_agents: list[str], app_version: str) -> list[str]:
    """Return a list of governance problems (empty list = cards are complete and up to date)."""
    problems: list[str] = []
    if cards.get("system", {}).get("version") != app_version:
        problems.append(f"system version {cards.get('system', {}).get('version')} != app version {app_version}")
    by_id = {a.get("id"): a for a in cards.get("agents", [])}
    for agent in graph_agents:
        if agent not in by_id:
            problems.append(f"agent '{agent}' has no card")
    for agent_id in by_id:
        if agent_id not in graph_agents:
            problems.append(f"card '{agent_id}' does not match any agent in the graph")
    for agent_id, card in by_id.items():
        for f in REQUIRED_AGENT_FIELDS:
            if f not in card or card[f] in ("", None):
                problems.append(f"card '{agent_id}' is missing field '{f}'")
    return problems
