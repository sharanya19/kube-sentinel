"""Hybrid runbook search: pgvector similarity + Postgres full-text search, merged with RRF.

Why hybrid: vector search understands meaning ("pod keeps running out of memory" finds the
OOMKilled runbook) but can miss exact tokens; keyword search nails exact tokens ("exit code 137",
"ImagePullBackOff") but misses paraphrases. Reciprocal Rank Fusion combines the two rankings
without having to calibrate their very different scores:  score = sum(1 / (60 + rank)).

    python -m rag.search "pod restarts with exit code 137"
"""

from __future__ import annotations

import sys
from typing import Any, Literal

from . import db
from .embed import embed_query

Mode = Literal["hybrid", "vector", "keyword"]
RRF_K = 60          # standard RRF constant: dampens the advantage of the very top ranks
CANDIDATES = 20     # how many chunks each method contributes before fusion


def _filters(category: str | None, component: str | None) -> tuple[str, list]:
    sql, params = "", []
    if category:
        sql += " AND category = %s"
        params.append(category)
    if component:
        sql += " AND (components && %s::text[])"
        params.append([component, "any"])
    return sql, params


def _vector_ranks(conn, query: str, where: str, params: list) -> list[str]:
    rows = conn.execute(
        f"SELECT id FROM runbook_chunks WHERE true{where} ORDER BY embedding <=> %s LIMIT {CANDIDATES}",
        [*params, embed_query(query)]).fetchall()
    return [r[0] for r in rows]


def _keyword_ranks(conn, query: str, where: str, params: list) -> list[str]:
    # OR the query terms together (websearch/plainto would AND them, so long queries match nothing).
    rows = conn.execute(
        f"""WITH q AS (SELECT to_tsquery('english',
                         replace(plainto_tsquery('english', %s)::text, '&', '|')) AS tsq)
            SELECT id FROM runbook_chunks, q
            WHERE tsv @@ q.tsq{where}
            ORDER BY ts_rank_cd(tsv, q.tsq) DESC LIMIT {CANDIDATES}""",
        [query, *params]).fetchall()
    return [r[0] for r in rows]


def search(query: str, k: int = 3, category: str | None = None, component: str | None = None,
           mode: Mode = "hybrid") -> list[dict[str, Any]]:
    """Top-k runbooks for a query, each with its best-matching section and a citation."""
    where, params = _filters(category, component)
    with db.connect() as conn:
        rankings = []
        if mode in ("hybrid", "vector"):
            rankings.append(_vector_ranks(conn, query, where, params))
        if mode in ("hybrid", "keyword"):
            rankings.append(_keyword_ranks(conn, query, where, params))

        scores: dict[str, float] = {}
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        if not scores:
            return []

        rows = conn.execute(
            "SELECT id, runbook_id, title, section, category, content FROM runbook_chunks WHERE id = ANY(%s)",
            [list(scores)]).fetchall()
        # Keep the best-scoring section per runbook, then the top-k runbooks.
        best: dict[str, dict[str, Any]] = {}
        for cid, rb, title, section, cat, content in rows:
            hit = {"runbook": rb, "title": title, "category": cat, "section": section,
                   "score": round(scores[cid], 4), "excerpt": content,
                   "citation": f"runbooks/{rb}.md#{section.lower().replace(' ', '-')}"}
            if rb not in best or hit["score"] > best[rb]["score"]:
                best[rb] = hit
        top = sorted(best.values(), key=lambda h: h["score"], reverse=True)[:k]

        # Attach each runbook's Fix section: that's what the agent needs to suggest a remediation.
        fixes = dict(conn.execute(
            "SELECT runbook_id, content FROM runbook_chunks WHERE section = 'Fix' AND runbook_id = ANY(%s)",
            [[h["runbook"] for h in top]]).fetchall())
        for h in top:
            h["fix"] = fixes.get(h["runbook"], "")
    return top


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "pod restarts with exit code 137"
    for mode in ("vector", "keyword", "hybrid"):
        hits = search(q, mode=mode)
        print(f"{mode:8} " + "  ".join(f"{h['runbook']}({h['section']})" for h in hits))
