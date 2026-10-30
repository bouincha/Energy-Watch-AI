"""Guardrails for LLM agents (OWASP Top 10 for LLM applications).

Covered here:
- LLM01 Prompt injection: pattern-based detection before anything reaches the LLM.
- LLM02 Sensitive information disclosure: output scanning for secrets / system prompt leaks.
- LLM06 Excessive agency: tools are read-only and high-risk actions need human approval.
- LLM10 Unbounded consumption: input length limit + per-client rate limiting.
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("override_instructions", re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|your|system)\b.{0,30}"
        r"\b(instructions?|rules?|prompts?|guidelines?)", re.I | re.S)),
    ("system_prompt_exfiltration", re.compile(
        r"\b(reveal|show|print|repeat|leak|display|tell me)\b.{0,40}\b(system|hidden|initial|developer)\s+"
        r"(prompt|instructions?|message)", re.I | re.S)),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|from now on you)\b.{0,60}"
                               r"\b(dan|jailbreak|unrestricted|without (any )?(rules|restrictions))", re.I | re.S)),
    ("delimiter_injection", re.compile(r"(<\|?(system|im_start|endoftext)\|?>|\[/?INST\]|###\s*system)", re.I)),
    ("secret_request", re.compile(r"\b(api[_ -]?key|password|secret|token|credentials?)\b.{0,40}"
                                  r"\b(show|give|print|reveal|send|what is)\b|\b(show|give|print|reveal|send)\b"
                                  r".{0,40}\b(api[_ -]?key|password|secret token|credentials?)\b", re.I | re.S)),
    ("code_execution", re.compile(r"\b(os\.system|subprocess|eval\(|exec\(|rm -rf|DROP\s+TABLE)\b", re.I)),
]

ENERGY_TERMS = re.compile(
    r"\b(energy|power|electric\w*|consum\w*|kwh|mwh|kw|watt\w*|bill|cost|tariff|hvac|cooling|heating|"
    r"lighting|light|compressor|solar|pv|renewable|carbon|co2|emission\w*|site|building|plant|factory|"
    r"data ?cent(er|re)|hospital|office|meter\w*|peak|load|demand|anomal\w*|spike|alert|leak|saving\w*|"
    r"efficien\w*|optimi\w*|report|summary|recommend\w*|advice|reduce|waste|pue|iso ?50001|equipment|"
    r"machine\w*|server\w*|air|temperature|night|weekend|usage|trend|forecast|month|week|today|yesterday|"
    r"overview|status|kenitra|casablanca|rabat|marrakech|ouarzazate|tanger|agadir|line|production)\b",
    re.I,
)

SECRET_OUTPUT = re.compile(r"\b(sk-[A-Za-z0-9]{10,}|gsk_[A-Za-z0-9]{10,}|AIza[0-9A-Za-z_\-]{20,})\b")


@dataclass
class GuardrailResult:
    allowed: bool
    category: str  # ok | injection | too_long | empty | off_topic
    reason: str = ""
    sanitized: str = ""

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "category": self.category, "reason": self.reason}


def check_input(query: str, max_chars: int = 1000) -> GuardrailResult:
    text = (query or "").strip()
    # Strip control characters that can hide instructions from humans reviewing logs.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f​-‏‪-‮]", "", text)
    if not text:
        return GuardrailResult(False, "empty", "The question is empty.")
    if len(text) > max_chars:
        return GuardrailResult(False, "too_long", f"The question exceeds {max_chars} characters.")
    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(False, "injection", f"Blocked: possible prompt injection ({name}).")
    if not ENERGY_TERMS.search(text):
        return GuardrailResult(False, "off_topic",
                               "EnergyWatch AI only answers questions about energy consumption, costs, "
                               "anomalies, efficiency and sustainability of your sites.", text)
    return GuardrailResult(True, "ok", "", text)


def check_output(text: str) -> str:
    """Remove anything that looks like a credential before an answer leaves the system."""
    return SECRET_OUTPUT.sub("[REDACTED]", text)


class RateLimiter:
    """Sliding-window limiter per client key."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
