"""Human-in-the-loop action queue with an append-only audit trail.

Agents never change equipment settings themselves. They *propose* actions; low-risk
actions are auto-approved by policy, medium/high-risk actions wait for an operator.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field

from app.core.observability import get_correlation_id, log_event, metrics, utcnow_iso

logger = logging.getLogger("energywatch.actions")

AUTO_APPROVE_RISK = {"low"}


@dataclass
class ProposedAction:
    id: str
    title: str
    site_id: str
    equipment: str
    risk: str  # low | medium | high
    estimated_savings_kwh_month: float
    estimated_savings_mad_month: float
    rationale: str
    proposed_by: str
    correlation_id: str | None
    status: str = "pending_approval"  # pending_approval | approved | rejected | auto_approved
    created_at: str = field(default_factory=utcnow_iso)
    decided_at: str | None = None
    decided_by: str | None = None
    comment: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditEntry:
    ts: str
    actor: str
    event: str
    target: str
    detail: str
    correlation_id: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ActionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: dict[str, ProposedAction] = {}
        self._audit: list[AuditEntry] = []
        self._seq = 0

    def propose(self, *, title: str, site_id: str, equipment: str, risk: str, savings_kwh: float,
                savings_mad: float, rationale: str, proposed_by: str) -> ProposedAction:
        with self._lock:
            # De-duplicate: the same pending action for the same equipment is not queued twice.
            for existing in self._actions.values():
                if (existing.title == title and existing.site_id == site_id
                        and existing.status in ("pending_approval", "auto_approved", "approved")):
                    return existing
            self._seq += 1
            action = ProposedAction(
                id=f"ACT-{self._seq:04d}", title=title, site_id=site_id, equipment=equipment, risk=risk,
                estimated_savings_kwh_month=round(savings_kwh, 1),
                estimated_savings_mad_month=round(savings_mad, 1), rationale=rationale,
                proposed_by=proposed_by, correlation_id=get_correlation_id(),
            )
            if risk in AUTO_APPROVE_RISK:
                action.status = "auto_approved"
                action.decided_at = utcnow_iso()
                action.decided_by = "policy:auto-approve-low-risk"
            self._actions[action.id] = action
            self._audit_unlocked(proposed_by, "action.proposed", action.id, f"{title} [{risk}]")
            if action.status == "auto_approved":
                self._audit_unlocked("policy", "action.auto_approved", action.id, "risk=low")
        metrics.inc("energywatch_actions_proposed_total", risk=risk)
        return action

    def decide(self, action_id: str, approve: bool, actor: str, comment: str | None = None) -> ProposedAction:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None:
                raise KeyError(action_id)
            if action.status != "pending_approval":
                raise ValueError(f"Action {action_id} is already {action.status}")
            action.status = "approved" if approve else "rejected"
            action.decided_at = utcnow_iso()
            action.decided_by = actor
            action.comment = comment
            self._audit_unlocked(actor, f"action.{action.status}", action_id, comment or "")
        metrics.inc("energywatch_actions_decided_total", decision=action.status)
        log_event(logger, f"action {action_id} {action.status} by {actor}", action_id=action_id)
        return action

    def list(self) -> list[ProposedAction]:
        with self._lock:
            return sorted(self._actions.values(), key=lambda a: a.id, reverse=True)

    def audit(self) -> list[AuditEntry]:
        with self._lock:
            return list(reversed(self._audit))

    def record(self, actor: str, event: str, target: str, detail: str) -> None:
        with self._lock:
            self._audit_unlocked(actor, event, target, detail)

    def clear(self) -> None:
        with self._lock:
            self._actions.clear()
            self._audit.clear()
            self._seq = 0

    def _audit_unlocked(self, actor: str, event: str, target: str, detail: str) -> None:
        self._audit.append(AuditEntry(utcnow_iso(), actor, event, target, detail, get_correlation_id()))


actions = ActionStore()
