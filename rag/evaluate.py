"""Measure runbook retrieval on labelled queries: Recall@1, Recall@3 and MRR per search mode.

    python -m rag.evaluate            # table for vector / keyword / hybrid
    python -m rag.evaluate --misses   # also list the queries a mode got wrong

Runs locally against Postgres; uses no LLM and no API quota.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from .search import search

QUERIES = Path(__file__).with_name("eval_queries.yaml")
MODES = ("vector", "keyword", "hybrid")


def evaluate(mode: str, cases: list[dict]) -> dict:
    r1 = r3 = rr = 0.0
    misses = []
    for case in cases:
        ranked = [h["runbook"] for h in search(case["query"], k=5, mode=mode)]
        rank = ranked.index(case["expect"]) + 1 if case["expect"] in ranked else None
        r1 += rank == 1
        r3 += rank is not None and rank <= 3
        rr += 1 / rank if rank else 0
        if rank != 1:
            misses.append((case["query"], case["expect"], ranked[:3]))
    n = len(cases)
    return {"recall@1": r1 / n, "recall@3": r3 / n, "mrr": rr / n, "misses": misses}


def main() -> None:
    cases = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
    results = {m: evaluate(m, cases) for m in MODES}
    print(f"{len(cases)} labelled queries, {len({c['expect'] for c in cases})} runbooks\n")
    print("| Search mode | Recall@1 | Recall@3 | MRR |")
    print("|---|---|---|---|")
    for m, r in results.items():
        print(f"| {m} | {r['recall@1']:.0%} | {r['recall@3']:.0%} | {r['mrr']:.2f} |")
    if "--misses" in sys.argv:
        for m, r in results.items():
            for q, exp, got in r["misses"]:
                print(f"\n[{m}] {q!r}\n   expected {exp}, got {got}")


if __name__ == "__main__":
    main()
