"""Application settings, loaded from environment variables (or a local .env file)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app import __version__

Provider = Literal["auto", "groq", "gemini", "openai", "anthropic", "mock"]

DEFAULT_MODELS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-5-5",
    "mock": "deterministic-mock",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "EnergyWatch AI"
    app_version: str = __version__
    environment: str = "development"
    log_level: str = "INFO"

    # --- LLM -----------------------------------------------------------------
    llm_provider: Provider = "auto"
    llm_model: str | None = None
    llm_temperature: float = 0.1
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 2
    groq_api_key: str | None = None
    google_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # --- Reliability -----------------------------------------------------------
    circuit_breaker_threshold: int = 3
    circuit_breaker_reset_s: float = 30.0

    # --- Monitoring / SLOs ----------------------------------------------------
    slo_latency_ms: float = 8000.0
    slo_error_rate: float = 0.2
    max_runs_kept: int = 500

    # --- Security ---------------------------------------------------------------
    admin_api_key: str | None = None  # protects approve/reject/chaos endpoints when set
    max_query_chars: int = 1000
    rate_limit_per_minute: int = 30

    # --- Quality page -----------------------------------------------------------
    github_repo: str = "bouincha/Energy-Watch-AI"  # CI status shown on the Quality page ("" disables)
    github_token: str | None = None  # optional, raises the GitHub API rate limit
    render_git_commit: str | None = None  # set automatically by Render

    # --- Data -------------------------------------------------------------------
    data_days: int = 30
    data_seed: int = 42

    @property
    def resolved_provider(self) -> str:
        """Pick a provider: explicit choice, or the first one with an API key, or the mock."""
        if self.llm_provider != "auto":
            return self.llm_provider
        for name, key in (
            ("groq", self.groq_api_key),
            ("gemini", self.google_api_key),
            ("openai", self.openai_api_key),
            ("anthropic", self.anthropic_api_key),
        ):
            if key:
                return name
        return "mock"

    @property
    def resolved_model(self) -> str:
        return self.llm_model or DEFAULT_MODELS[self.resolved_provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
