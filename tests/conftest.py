"""Shared fixtures. Tests always run with the deterministic mock LLM (no API key, no network)."""

import os

os.environ.update({
    "LLM_PROVIDER": "mock",
    "LLM_MAX_RETRIES": "0",
    "RATE_LIMIT_PER_MINUTE": "10000",
    "LOG_LEVEL": "WARNING",
    "ADMIN_API_KEY": "",
    "GITHUB_REPO": "",  # never call the GitHub API from tests
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.quality import clear_cache  # noqa: E402
from app.api.routes import rate_limiter  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.actions import actions  # noqa: E402
from app.core.incidents import incidents  # noqa: E402
from app.core.observability import metrics, runs  # noqa: E402
from app.core.resilience import chaos  # noqa: E402
from app.llm.gateway import reset_gateway  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts with empty stores, no chaos and a fresh LLM gateway."""
    get_settings.cache_clear()
    runs.clear()
    incidents.clear()
    actions.clear()
    metrics.reset()
    chaos.reset()
    rate_limiter.reset()
    clear_cache()
    gw = reset_gateway()
    gw.breaker.reset_after_s = 0.3  # fast recovery for incident tests
    yield
    chaos.reset()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
