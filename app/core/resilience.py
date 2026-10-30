"""Reliability primitives: circuit breaker, retry with backoff and chaos injection."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.observability import log_event, metrics

logger = logging.getLogger("energywatch.resilience")

T = TypeVar("T")


class CircuitOpenError(RuntimeError):
    """Raised when a call is refused because the circuit breaker is open."""


class ChaosError(RuntimeError):
    """Failure injected on purpose by the chaos controller (incident drills)."""


class CircuitBreaker:
    """Classic three-state breaker: closed -> open (after N failures) -> half-open -> closed."""

    def __init__(self, name: str, threshold: int = 3, reset_after_s: float = 30.0) -> None:
        self.name = name
        self.threshold = threshold
        self.reset_after_s = reset_after_s
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._listeners: list[Callable[[str, str], None]] = []

    def on_transition(self, callback: Callable[[str, str], None]) -> None:
        self._listeners.append(callback)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state_unlocked()

    def _state_unlocked(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.reset_after_s:
            return "half_open"
        return "open"

    @property
    def failures(self) -> int:
        return self._failures

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            previous = self._state_unlocked()
            self._failures = 0
            self._opened_at = None
        if previous != "closed":
            self._notify(previous, "closed")

    def record_failure(self) -> None:
        with self._lock:
            previous = self._state_unlocked()
            self._failures += 1
            opened = False
            if previous == "half_open" or self._failures >= self.threshold:
                self._opened_at = time.monotonic()
                opened = previous != "open"
        if opened:
            self._notify(previous, "open")

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def _notify(self, old: str, new: str) -> None:
        metrics.inc("energywatch_circuit_transitions_total", breaker=self.name, to=new)
        log_event(logger, f"circuit breaker '{self.name}' {old} -> {new}",
                  logging.WARNING if new == "open" else logging.INFO,
                  breaker=self.name, old_state=old, new_state=new)
        for cb in list(self._listeners):
            try:
                cb(old, new)
            except Exception:  # listeners must never break the caller
                logger.exception("circuit listener failed")


def retry_call(fn: Callable[[], T], attempts: int, base_delay_s: float = 0.3,
               retry_on: tuple[type[BaseException], ...] = (Exception,)) -> T:
    """Call ``fn`` up to ``attempts`` times with exponential backoff."""
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203 - retry loop
            last = exc
            if i < attempts - 1:
                time.sleep(base_delay_s * (2 ** i))
    assert last is not None
    raise last


@dataclass
class ChaosState:
    llm_outage: bool = False
    llm_latency_ms: int = 0
    tool_failure: bool = False

    def to_dict(self) -> dict:
        return {"llm_outage": self.llm_outage, "llm_latency_ms": self.llm_latency_ms,
                "tool_failure": self.tool_failure}


class ChaosController:
    """Lets operators inject failures to rehearse the incident runbook (game days)."""

    def __init__(self) -> None:
        self.state = ChaosState()
        self._lock = threading.Lock()

    def update(self, **changes) -> ChaosState:
        with self._lock:
            for key, value in changes.items():
                if value is not None and hasattr(self.state, key):
                    setattr(self.state, key, value)
        log_event(logger, "chaos configuration changed", logging.WARNING, **self.state.to_dict())
        return self.state

    def reset(self) -> None:
        with self._lock:
            self.state = ChaosState()

    def before_llm_call(self) -> None:
        s = self.state
        if s.llm_latency_ms:
            time.sleep(s.llm_latency_ms / 1000)
        if s.llm_outage:
            raise ChaosError("Injected LLM provider outage (chaos drill)")

    def before_tool_call(self, tool: str) -> None:
        if self.state.tool_failure:
            raise ChaosError(f"Injected failure in tool '{tool}' (chaos drill)")


chaos = ChaosController()
