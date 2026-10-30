"""Quality page: CI status, live evaluation gates and self-checks."""

from app.api import quality
from app.config import get_settings
from app.core.resilience import chaos

FAKE_RUN = {"workflow_runs": [{
    "id": 1, "name": "CI/CD", "status": "completed", "conclusion": "success", "head_sha": "62e09e3abcdef",
    "head_commit": {"message": "EnergyWatch AI v1.0.0\n\nbody"}, "event": "push",
    "run_started_at": "2026-10-30T11:27:30Z", "html_url": "https://github.com/x/y/actions/runs/1"}]}
FAKE_JOBS = {"jobs": [{
    "name": "Lint, tests & evaluation", "status": "completed", "conclusion": "success",
    "started_at": "2026-10-30T11:27:32Z", "completed_at": "2026-10-30T11:27:58Z", "html_url": "u",
    "steps": [{"name": "Set up job", "conclusion": "success"},
              {"name": "Tests with coverage", "conclusion": "success"}]}]}


def fake_github(path):
    return FAKE_JOBS if path.endswith("/jobs") else FAKE_RUN


def test_quality_without_github(client):
    body = client.get("/api/quality").json()
    assert body["ci"]["available"] is False
    assert body["evaluation"]["passed"] is True
    assert all(c["passed"] for c in body["self_checks"])
    assert body["overall"] == "warning"  # everything green, but CI status unknown


def test_quality_all_passing_with_ci(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_repo", "bouincha/Energy-Watch-AI")
    monkeypatch.setattr(get_settings(), "render_git_commit", "62e09e3abcdef0123")
    monkeypatch.setattr(quality, "_github_get", fake_github)
    body = client.get("/api/quality?refresh=true").json()
    ci = body["ci"]
    assert ci["conclusion"] == "success" and ci["commit"] == "62e09e3"
    assert ci["commit_message"] == "EnergyWatch AI v1.0.0"
    assert [s["name"] for s in ci["jobs"][0]["steps"]] == ["Tests with coverage"]  # runner noise removed
    names = [c["name"] for c in body["self_checks"]]
    assert "Deployed commit verified by CI" in names
    assert body["overall"] == "passing"


def test_quality_failing_ci_is_reported(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_repo", "x/y")
    failed = {"workflow_runs": [{**FAKE_RUN["workflow_runs"][0], "conclusion": "failure"}]}
    monkeypatch.setattr(quality, "_github_get", lambda p: FAKE_JOBS if p.endswith("/jobs") else failed)
    assert client.get("/api/quality?refresh=true").json()["overall"] == "failing"


def test_github_unreachable_is_not_fatal(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "github_repo", "x/y")

    def boom(path):
        raise OSError("network down")

    monkeypatch.setattr(quality, "_github_get", boom)
    body = client.get("/api/quality?refresh=true").json()
    assert body["ci"]["available"] is False and "unreachable" in body["ci"]["reason"]


def test_open_incident_shows_as_warning(client):
    chaos.update(tool_failure=True)
    client.post("/api/agent/ask", json={"query": "consumption of the plant"})
    checks = {c["name"]: c for c in client.get("/api/quality").json()["self_checks"]}
    assert checks["Open incidents"]["passed"] is False and checks["Open incidents"]["level"] == "warning"
