"""LLM gateway: one entry point for every model call made by the agents.

Responsibilities
- choose the provider (Groq, Gemini, OpenAI, Anthropic, or the deterministic mock)
- enforce timeouts, retries with backoff and a circuit breaker
- apply chaos injection (incident drills)
- record metrics and attach every call to the current run (correlation ID)

When the provider is unavailable the gateway raises ``LLMUnavailable``; agents catch it
and fall back to a deterministic answer flagged as *degraded* (runbook RB-001).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.core.incidents import incidents
from app.core.observability import Timer, get_correlation_id, log_event, metrics, runs
from app.core.resilience import ChaosError, CircuitBreaker, chaos, retry_call

logger = logging.getLogger("energywatch.llm")

M = TypeVar("M", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """The model could not be reached (breaker open, retries exhausted, chaos drill...)."""


def _build_chat_model(settings: Settings):
    provider = settings.resolved_provider
    common: dict[str, Any] = {"temperature": settings.llm_temperature}
    model = settings.resolved_model
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(model=model, api_key=settings.groq_api_key, timeout=settings.llm_timeout_s,
                        max_retries=0, **common)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=model, google_api_key=settings.google_api_key,
                                      timeout=settings.llm_timeout_s, max_retries=0, **common)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, api_key=settings.openai_api_key, timeout=settings.llm_timeout_s,
                          max_retries=0, **common)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, api_key=settings.anthropic_api_key,
                             default_request_timeout=settings.llm_timeout_s, max_retries=0, **common)
    return None  # mock


class LLMGateway:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.provider = self.settings.resolved_provider
        self.model_name = self.settings.resolved_model
        self.breaker = CircuitBreaker("llm", self.settings.circuit_breaker_threshold,
                                      self.settings.circuit_breaker_reset_s)
        self.breaker.on_transition(incidents.on_llm_breaker)
        self._model = None
        self._model_error: str | None = None

    @property
    def is_mock(self) -> bool:
        return self.provider == "mock"

    def _chat_model(self):
        if self._model is None and not self.is_mock:
            self._model = _build_chat_model(self.settings)
        return self._model

    def status(self) -> dict:
        return {"provider": self.provider, "model": self.model_name, "circuit_breaker": self.breaker.state,
                "consecutive_failures": self.breaker.failures, "mock": self.is_mock}

    # ------------------------------------------------------------------ core call
    def _call(self, agent: str, operation: str, fn: Callable[[], Any]) -> Any:
        cid = get_correlation_id()
        run = runs.get(cid) if cid else None
        if not self.breaker.allow():
            metrics.inc("energywatch_llm_calls_total", agent=agent, outcome="short_circuited")
            raise LLMUnavailable("circuit breaker open")

        def attempt():
            chaos.before_llm_call()
            return fn()

        if run:
            run.llm_calls += 1
        with Timer() as t:
            try:
                result = retry_call(attempt, attempts=self.settings.llm_max_retries + 1, base_delay_s=0.2)
            except Exception as exc:
                self.breaker.record_failure()
                if run:
                    run.llm_failures += 1
                metrics.inc("energywatch_llm_calls_total", agent=agent, outcome="error")
                kind = "chaos" if isinstance(exc, ChaosError) else type(exc).__name__
                log_event(logger, f"LLM call failed for {agent}: {exc}", logging.WARNING,
                          agent=agent, operation=operation, error_type=kind)
                raise LLMUnavailable(str(exc)) from exc
        self.breaker.record_success()
        metrics.inc("energywatch_llm_calls_total", agent=agent, outcome="ok")
        metrics.observe("energywatch_llm_latency_ms", t.ms, agent=agent)
        usage = getattr(result, "usage_metadata", None) or {}
        if usage:
            metrics.inc("energywatch_llm_tokens_total", usage.get("input_tokens", 0), kind="input")
            metrics.inc("energywatch_llm_tokens_total", usage.get("output_tokens", 0), kind="output")
        return result

    # ------------------------------------------------------------------ public helpers
    def structured(self, agent: str, schema: type[M], system: str, user: str,
                   mock: Callable[[], M]) -> M:
        """Structured output (Pydantic schema). ``mock`` produces the deterministic answer."""
        if self.is_mock:
            return self._call(agent, "structured", mock)
        model = self._chat_model().with_structured_output(schema)
        return self._call(agent, "structured", lambda: model.invoke([("system", system), ("human", user)]))

    def write(self, agent: str, system: str, user: str, mock: Callable[[], str]) -> str:
        """Free-text generation."""
        if self.is_mock:
            return self._call(agent, "write", mock)
        msg = self._call(agent, "write", lambda: self._chat_model().invoke([("system", system), ("human", user)]))
        content = msg.content
        if isinstance(content, list):  # some providers return content blocks
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        return str(content).strip()

    def choose_tools(self, agent: str, system: str, user: str, tools: list,
                     mock: Callable[[], list[dict]]) -> list[dict]:
        """Tool calling: the model picks which tools to call and with which arguments."""
        if self.is_mock:
            return self._call(agent, "tool_choice", mock)
        model = self._chat_model().bind_tools(tools)
        msg = self._call(agent, "tool_choice", lambda: model.invoke([("system", system), ("human", user)]))
        return [{"name": c["name"], "args": c.get("args", {})} for c in (msg.tool_calls or [])]


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway


def reset_gateway(settings: Settings | None = None) -> LLMGateway:
    global _gateway
    _gateway = LLMGateway(settings)
    return _gateway
