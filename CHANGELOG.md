# Changelog

All notable changes to EnergyWatch AI. Versions follow [Semantic Versioning](https://semver.org).
System version, agent-card versions and prompt version are bumped together.

## [1.2.0] - 2026-10-30

### Changed
- Dashboard redesigned for non-experts: light theme by default, plain-language navigation
  (Home, Ask the AI, Problems found, Approvals), a Home page that explains the situation in one
  sentence with an energy health score, problems described as "what happened / what to do",
  and an explanation box on technical pages.

### Added
- "What is this page?" help panel on every page and a welcome guide on the first visit.
- `start-online.bat`: one double-click to run the app and publish it with Cloudflare Tunnel.
- `start-local.bat` and `docs/GO_ONLINE.md`.

## [1.1.0] - 2026-10-30

### Added
- **Quality** dashboard page and `GET /api/quality`: latest GitHub Actions run (jobs and steps),
  evaluation quality gates run live on the server, and runtime self-checks (graph, agent cards,
  data, RAG, guardrails canary, LLM breaker, open incidents, deployed commit vs. CI commit).
- Settings `GITHUB_REPO`, `GITHUB_TOKEN`; `RENDER_GIT_COMMIT` is read automatically on Render.
- 5 tests for the quality report (70 in total).

## [1.0.0] - 2026-10-30 · alpha (production pilot)

### Added
- LangGraph multi-agent system: guardrails, supervisor (planner), consumption analyst
  (tool calling), anomaly detector, energy advisor (RAG), report writer, monitoring agent.
- LLM gateway with Groq / Gemini / OpenAI / Anthropic and a deterministic mock; timeouts, retries,
  circuit breaker, chaos injection.
- Energy dataset simulation (4 sites, 16 meters, 30 days hourly) with 5 injected anomalies and
  statistical detection (robust z-score, pattern classification).
- RAG knowledge base (7 guides) with heading-aware chunking and BM25 retrieval.
- Human-in-the-loop action queue with risk-based approval policy and audit trail.
- Observability: correlation IDs, JSON logs, spans, Prometheus metrics, run explorer.
- Incident management with runbooks RB-001 to RB-004, automatic detection and resolution, MTTR.
- Governance: machine-readable agent cards validated in CI.
- Dashboard: Overview, AI Assistant, Anomalies, Actions, Monitoring, Governance (dark/light, responsive).
- 65 automated tests, evaluation benchmark, GitHub Actions CI/CD, Docker image, Render blueprint.

### Prompt versions
- `2026.10.1`: initial prompts for supervisor, analyst, anomaly writer, advisor, report writer.

## Roadmap
- 1.2: persistent telemetry (PostgreSQL / OpenTelemetry), larger multilingual evaluation set.
- 1.3: multimodal agent that reads meter photos and equipment nameplates (vision + text).
- 1.4: real metering connector (MQTT / utility API) and consumption forecasting.
