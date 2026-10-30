# Course mapping: where each week's concepts live in the code

| Week | Topic | Where in EnergyWatch AI |
|---|---|---|
| **S1** | Agentic loop & model taxonomy | `app/agents/graph.py`: plan → act (tools) → observe → answer; multi-provider gateway `app/llm/gateway.py` |
| | Tool calling: how the agent chooses a tool | `consumption_analyst_node` uses `bind_tools(ANALYST_TOOLS)` and the LLM picks the tools; `app/agents/tools.py` validates the arguments |
| | Stable prompts, structured outputs, routing | Versioned prompts `app/agents/prompts.py`; `RoutingDecision` Pydantic schema + `with_structured_output`; `_sanitize_decision` |
| | Memory & state | `EnergyState` with reducers (`trace`, `completed`, `errors`, `degraded`); run store; chat history in the dashboard |
| **S2** | RAG fundamentals | `app/rag/`: 7 Markdown guides, used by `energy_advisor_node` with citations |
| | Advanced RAG: chunking & search | Heading-aware chunking with overlapping windows, BM25 scoring, domain query expansion (`SYNONYMS`), relevance threshold |
| | Structured extraction | Raw hourly meter data turned into structured `AnomalyEvent` records (pattern, cost, severity) |
| | LLM agent security (OWASP) | `app/core/security.py`: LLM01 injection, LLM02 leakage (output + log redaction), LLM06 least privilege + HITL, LLM10 rate limit and length |
| **S3** | Agentic architectures: planning | Supervisor / planner pattern + dispatcher `next_step()` |
| | Reliability engineering | Timeouts, retries with backoff, deterministic fallbacks, degraded mode |
| | Resilience in the architecture | `app/core/resilience.py`: circuit breaker (closed / open / half-open), chaos controller |
| | Evaluation & benchmarking | `app/evaluation/`: golden sets, precision/recall, hit@3, latency, CI quality gates |
| **S4** | Multi-agent architectures & patterns | 7 agents in one LangGraph `StateGraph` (supervisor pattern) |
| | Multi-agent framework | LangGraph (conditional edges, reducers, compiled graph, Mermaid export) |
| | Business agents, human-in-the-loop & audit | `app/core/actions.py`: risk-based approval policy, Approve/Reject in the UI, append-only audit trail |
| | Multimodal agents | Not in scope for v1.0; see the roadmap in CHANGELOG (meter photo / nameplate reading) |
| **S5** | Agent observability | Correlation IDs, JSON logs, spans, Prometheus `/metrics`, Monitoring page |
| | Automated tests & agentic CI/CD | 70 pytest tests, `.github/workflows/ci.yml`, smoke tests before and after deploy |
| | Governance, agent cards & versioning | `app/governance/agent_cards.json` validated in CI, `docs/AGENT_CARD.md`, `CHANGELOG.md` |
| | Deployment: containerization & cloud | `Dockerfile`, `docker-compose.yml`, `render.yaml`, `docs/DEPLOYMENT.md` |
