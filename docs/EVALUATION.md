# Evaluation & Benchmarking

Run it with: `python -m app.evaluation.run_eval --json reports/eval.json`. It runs in CI on every
push, and the report is uploaded as an artifact. Quality gates are enforced by
`tests/test_governance_eval.py::test_evaluation_quality_gates`.

## Results (v1.0.0, deterministic mock LLM)

| Component | Metric | Result | Gate |
|---|---|---|---|
| Supervisor routing | Intent accuracy (20 questions) | **100 %** | ≥ 90 % |
| | Site accuracy | **100 %** | ≥ 90 % |
| | Time-window accuracy | **100 %** | - |
| Anomaly detector | Precision / recall / F1 (3 seeds × 5 injected anomalies) | **1.00 / 1.00 / 1.00** | F1 ≥ 0.9 |
| | Robustness: 30 seeds × 7 calendar alignments | **0 mismatches / 210** | tested |
| RAG retriever | Hit rate @3 (10 questions) | **100 %** | ≥ 90 % |
| Guardrails | Accuracy (7 attacks / off-topic + 3 legitimate) | **100 %** | = 100 % |
| End-to-end | Latency p50 / p95 (mock LLM, 10 runs) | ≈ 5 ms / ≈ 50 ms | SLO 8 s |

## Method

- **Routing:** `app/evaluation/golden_set.json → routing`. Each question has an expected intent,
  and optionally a site and window. It is evaluated on the deterministic planner that also serves
  as the fallback when the LLM is down.
- **Anomaly detection:** the simulation injects 5 anomalies with a known meter and pattern. A
  detection counts as a true positive only if both the meter **and** the pattern classification
  match.
- **Retrieval:** a hit means the expected document appears in the top 3 BM25 results.
- **Guardrails:** injection and off-topic prompts must be blocked; legitimate energy questions
  must pass.

## Limitations (honest reading of the numbers)

- The golden sets are **small and were written by the team** while building the rules, so 100 %
  shows the system behaves as designed. It is not a measure of generalization. More varied
  phrasings would lower the routing accuracy of the keyword planner. That is exactly why a real
  LLM planner is used when a key is configured, with the rules only as a fallback.
- The anomaly data is **simulated**. Real meters have more noise, missing data and legitimate
  schedule changes.
- With a real LLM, answers are non-deterministic. We evaluate the **structure** (plan, tools
  called, citations present, numbers taken from the tools) rather than the exact wording.

## Ideas for v1.1
- Add 100+ paraphrased questions, including French and Darija, to the routing set.
- LLM-as-judge for answer faithfulness: every number in the answer must appear in the tool results.
- Evaluate on a public metering dataset with labelled anomalies.
