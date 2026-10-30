# Changelog

All notable changes to EnergyWatch AI. Versions follow [Semantic Versioning](https://semver.org).
System version, agent-card versions and prompt version are bumped together.

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
- 1.1: persistent telemetry (PostgreSQL / OpenTelemetry), larger multilingual evaluation set.
- 1.2: multimodal agent that reads meter photos and equipment nameplates (vision + text).
- 1.3: real metering connector (MQTT / utility API) and consumption forecasting.
