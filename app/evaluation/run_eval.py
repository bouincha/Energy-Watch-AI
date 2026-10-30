"""Offline evaluation / benchmark of EnergyWatch AI.

Usage:  python -m app.evaluation.run_eval [--json reports/eval.json]

Measures
- routing accuracy of the supervisor (intent, site, window) on a golden set
- precision / recall of the anomaly detector against the injected ground truth
- retrieval hit rate@3 of the RAG knowledge base
- guardrail accuracy (attacks blocked, legitimate questions allowed)
- end-to-end latency of the full graph
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from app.agents.nodes import _sanitize_decision, rule_route
from app.core.security import check_input
from app.data.energy_data import build_dataset
from app.rag.retriever import get_retriever

GOLDEN = Path(__file__).parent / "golden_set.json"


def eval_routing(cases: list[dict]) -> dict:
    ok_intent = ok_site = ok_days = n_site = n_days = 0
    failures = []
    for c in cases:
        d = _sanitize_decision(rule_route(c["query"]), None, c["query"])
        good = d.intent == c["intent"]
        ok_intent += good
        if "site_id" in c:
            n_site += 1
            ok_site += d.site_id == c["site_id"]
            good &= d.site_id == c["site_id"]
        if "days" in c:
            n_days += 1
            ok_days += d.days == c["days"]
            good &= d.days == c["days"]
        if not good:
            failures.append({"query": c["query"], "expected": c, "got": d.model_dump()})
    return {"cases": len(cases), "intent_accuracy": round(ok_intent / len(cases), 3),
            "site_accuracy": round(ok_site / max(n_site, 1), 3), "window_accuracy": round(ok_days / max(n_days, 1), 3),
            "failures": failures}


def eval_anomalies(seeds: tuple[int, ...] = (42, 7, 2026)) -> dict:
    tp = fp = fn = 0
    for seed in seeds:
        ds = build_dataset(seed=seed)
        detected = ds.anomalies()
        truth = {(a.meter_id, a.kind) for a in ds.injected}
        found = {(a.meter_id, a.pattern) for a in detected}
        tp += len(truth & found)
        fn += len(truth - found)
        fp += len(found - truth)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    return {"seeds": list(seeds), "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(precision, 3), "recall": round(recall, 3),
            "f1": round(2 * precision * recall / max(precision + recall, 1e-9), 3)}


def eval_retrieval(cases: list[dict], k: int = 3) -> dict:
    r = get_retriever()
    hits = 0
    misses = []
    for c in cases:
        docs = [x["doc"] for x in r.search(c["query"], k=k)]
        if c["expected_doc"] in docs:
            hits += 1
        else:
            misses.append({"query": c["query"], "expected": c["expected_doc"], "got": docs})
    return {"cases": len(cases), f"hit_rate_at_{k}": round(hits / len(cases), 3), "misses": misses}


def eval_guardrails(cases: list[dict]) -> dict:
    correct = sum(check_input(c["query"]).allowed == c["allowed"] for c in cases)
    return {"cases": len(cases), "accuracy": round(correct / len(cases), 3)}


def eval_latency(queries: list[str]) -> dict:
    from app.agents.graph import run_agent

    durations = []
    for q in queries:
        t = time.perf_counter()
        run_agent(q)
        durations.append((time.perf_counter() - t) * 1000)
    durations.sort()
    return {"runs": len(durations), "p50_ms": round(statistics.median(durations), 1),
            "p95_ms": round(durations[min(len(durations) - 1, int(len(durations) * 0.95))], 1)}


def run_all(with_latency: bool = True) -> dict:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    report = {
        "routing": eval_routing(golden["routing"]),
        "anomaly_detection": eval_anomalies(),
        "retrieval": eval_retrieval(golden["retrieval"]),
        "guardrails": eval_guardrails(golden["guardrails"]),
    }
    if with_latency:
        report["latency"] = eval_latency([c["query"] for c in golden["routing"][:10]])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write the report to this file")
    args = parser.parse_args()
    from app.core.observability import configure_logging

    configure_logging("ERROR")
    report = run_all()
    r, a, q, g = report["routing"], report["anomaly_detection"], report["retrieval"], report["guardrails"]
    print("EnergyWatch AI - evaluation report")
    print(f"  Routing     intent {r['intent_accuracy']:.0%} | site {r['site_accuracy']:.0%} | "
          f"window {r['window_accuracy']:.0%}  ({r['cases']} cases)")
    print(f"  Anomalies   precision {a['precision']:.0%} | recall {a['recall']:.0%} | F1 {a['f1']:.2f}")
    print(f"  Retrieval   hit@3 {q['hit_rate_at_3']:.0%}  ({q['cases']} cases)")
    print(f"  Guardrails  accuracy {g['accuracy']:.0%}  ({g['cases']} cases)")
    if "latency" in report:
        print(f"  Latency     p50 {report['latency']['p50_ms']} ms | p95 {report['latency']['p95_ms']} ms")
    for f in r["failures"]:
        print("  ! routing failure:", f["query"], "->", f["got"]["intent"], f["got"]["site_id"], f["got"]["days"])
    for m in q["misses"]:
        print("  ! retrieval miss:", m)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
