"""Observability: correlation IDs, structured JSON logs, metrics and the run store.

Every request that reaches the agent system gets a correlation ID. It is stored in a
context variable, so every log line, span, metric sample, incident and audit entry
produced while serving that request carries the same ID.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_CID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")


def new_correlation_id() -> str:
    return f"ewa-{uuid.uuid4().hex[:20]}"


def sanitize_correlation_id(value: str | None) -> str:
    """Accept a caller-provided ID only if it is safe to log; otherwise mint a new one."""
    if value and _CID_RE.match(value):
        return value
    return new_correlation_id()


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str):
    return _correlation_id.set(value)


def reset_correlation_id(token) -> None:
    _correlation_id.reset(token)


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ----------------------------------------------------------------------------- logging

_REDACT_PATTERNS = [
    (re.compile(r"\b(sk|gsk|AIza)[A-Za-z0-9_\-]{10,}\b"), "[REDACTED_KEY]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+212|0)[5-7](?:[ \-]?\d){8}(?!\d)"), "[REDACTED_PHONE]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": utcnow_iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "correlation_id": getattr(record, "correlation_id", None) or get_correlation_id(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, msg: str, level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})


# ----------------------------------------------------------------------------- metrics


class Metrics:
    """Tiny thread-safe metrics registry with Prometheus text exposition."""

    _BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self.histograms: dict[tuple[str, tuple], list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            series = self.histograms[(name, tuple(sorted(labels.items())))]
            series.append(value)
            if len(series) > 5000:
                del series[:1000]

    def counter_value(self, name: str, **labels: str) -> float:
        """Sum of every series of ``name`` whose labels include the given ones."""
        wanted = set(labels.items())
        with self._lock:
            return sum(v for (n, lbl), v in self.counters.items() if n == name and wanted <= set(lbl))

    def reset(self) -> None:
        with self._lock:
            self.counters.clear()
            self.histograms.clear()

    @staticmethod
    def _fmt_labels(labels: tuple, extra: dict[str, str] | None = None) -> str:
        items = list(labels) + list((extra or {}).items())
        if not items:
            return ""
        return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}"

    def prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            seen: set[str] = set()
            for (name, labels), value in sorted(self.counters.items()):
                if name not in seen:
                    lines.append(f"# TYPE {name} counter")
                    seen.add(name)
                lines.append(f"{name}{self._fmt_labels(labels)} {value}")
            for (name, labels), values in sorted(self.histograms.items()):
                if name not in seen:
                    lines.append(f"# TYPE {name} histogram")
                    seen.add(name)
                for b in self._BUCKETS:
                    count = sum(1 for v in values if v <= b)
                    lines.append(f"{name}_bucket{self._fmt_labels(labels, {'le': str(b)})} {count}")
                lines.append(f"{name}_bucket{self._fmt_labels(labels, {'le': '+Inf'})} {len(values)}")
                lines.append(f"{name}_sum{self._fmt_labels(labels)} {sum(values)}")
                lines.append(f"{name}_count{self._fmt_labels(labels)} {len(values)}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


# ----------------------------------------------------------------------------- runs & spans


@dataclass
class Span:
    agent: str
    started_at: str
    duration_ms: float
    status: str  # ok | error | degraded | skipped
    summary: str = ""
    tool_calls: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class RunRecord:
    correlation_id: str
    query: str
    started_at: str
    status: str = "running"  # running | ok | degraded | blocked | error
    intent: str | None = None
    plan: list[str] = field(default_factory=list)
    spans: list[Span] = field(default_factory=list)
    duration_ms: float = 0.0
    llm_provider: str = ""
    llm_calls: int = 0
    llm_failures: int = 0
    health: dict[str, Any] = field(default_factory=dict)
    answer_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunStore:
    """Keeps the most recent agent executions in memory for the dashboard and the API."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lock = threading.Lock()
        self._runs: deque[RunRecord] = deque(maxlen=maxlen)
        self._index: dict[str, RunRecord] = {}

    def start(self, correlation_id: str, query: str, provider: str) -> RunRecord:
        run = RunRecord(correlation_id=correlation_id, query=redact(query)[:300],
                        started_at=utcnow_iso(), llm_provider=provider)
        with self._lock:
            if len(self._runs) == self._runs.maxlen:
                old = self._runs[0]
                self._index.pop(old.correlation_id, None)
            self._runs.append(run)
            self._index[correlation_id] = run
        return run

    def get(self, correlation_id: str) -> RunRecord | None:
        with self._lock:
            return self._index.get(correlation_id)

    def add_span(self, correlation_id: str | None, span: Span) -> None:
        if not correlation_id:
            return
        with self._lock:
            run = self._index.get(correlation_id)
            if run is not None:
                run.spans.append(span)

    def list(self, limit: int = 50) -> list[RunRecord]:
        with self._lock:
            return list(reversed(self._runs))[:limit]

    def all(self) -> list[RunRecord]:
        with self._lock:
            return list(self._runs)

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._index.clear()


class Timer:
    def __enter__(self) -> Timer:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.ms = (time.perf_counter() - self.start) * 1000

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000


runs = RunStore()
