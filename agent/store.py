"""Incident reports: stored in Postgres (the same database as the runbooks).

Each report is one row: a few columns for filtering (status, component, category, created_at) plus
the full report as JSONB. Status changes are single atomic UPDATEs, so a report can't be approved
twice even from two browser tabs or two replicas of the UI.

Status flow:
    pending_approval --Approve--> fixing --> resolved | fix_failed
                     --Reject---> rejected

    python -m agent.store import reports/      # import old JSON report files
    python -m agent.store stats                # incidents per component, time to resolve

KUBE_SENTINEL_STORE=files keeps reports as JSON files in reports/ instead (no database needed).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = os.environ.get("KUBE_SENTINEL_STORE", "postgres")
REPORTS_DIR = Path(os.environ.get("KUBE_SENTINEL_REPORTS_DIR", ROOT / "reports"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id          text PRIMARY KEY,
    created_at  timestamptz NOT NULL,
    status      text NOT NULL,
    component   text,
    category    text,
    source      text,
    data        jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS incidents_created ON incidents (created_at DESC);
CREATE INDEX IF NOT EXISTS incidents_status ON incidents (status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_id(report_id: str) -> str:
    if not report_id.replace("-", "").isalnum():  # ids look like 20261112-133918-9cf7f0
        raise KeyError(report_id)
    return report_id


# ---------------- Postgres ----------------

_schema_ready = False
_schema_lock = threading.Lock()


def _conn():
    global _schema_ready
    from rag import db  # lazy: the files backend needs no database libraries
    conn = db.connect()
    if not _schema_ready:
        with _schema_lock:
            conn.execute(SCHEMA)
            _schema_ready = True
    return conn


def _columns(report: dict[str, Any]) -> tuple:
    rep = report.get("report", {})
    return (report["id"], report.get("created_at") or now(), report.get("status", "pending_approval"),
            rep.get("component"), rep.get("category"), report.get("source"))


def _pg_save(report: dict[str, Any]) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO incidents (id, created_at, status, component, category, source, data)
               VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, data = EXCLUDED.data""",
            [*_columns(report), json.dumps(report, default=str)])


def _pg_list(limit: int) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT data FROM incidents ORDER BY created_at DESC LIMIT %s", [limit]).fetchall()
    return [r[0] for r in rows]


def _pg_get(report_id: str) -> dict[str, Any]:
    with _conn() as conn:
        row = conn.execute("SELECT data FROM incidents WHERE id = %s", [report_id]).fetchone()
    if not row:
        raise KeyError(report_id)
    return row[0]


def _pg_update(report_id: str, fields: dict[str, Any], only_if_status: str | None) -> dict[str, Any] | None:
    sql = """UPDATE incidents
             SET data = data || %s::jsonb, status = COALESCE(%s, status)
             WHERE id = %s""" + (" AND status = %s" if only_if_status else "") + " RETURNING data"
    params = [json.dumps(fields, default=str), fields.get("status"), report_id]
    if only_if_status:
        params.append(only_if_status)
    with _conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


# ---------------- JSON files (fallback) ----------------

_file_lock = threading.Lock()


def _path(report_id: str) -> Path:
    return REPORTS_DIR / f"{report_id}.json"


def _file_save(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    with _file_lock:
        _path(report["id"]).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def _file_list(limit: int) -> list[dict[str, Any]]:
    out = []
    for p in REPORTS_DIR.glob("*.json") if REPORTS_DIR.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)[:limit]


def _file_get(report_id: str) -> dict[str, Any]:
    p = _path(report_id)
    if not p.exists():
        raise KeyError(report_id)
    return json.loads(p.read_text(encoding="utf-8"))


def _file_update(report_id: str, fields: dict[str, Any], only_if_status: str | None) -> dict[str, Any] | None:
    with _file_lock:
        report = _file_get(report_id)
        if only_if_status and report.get("status") != only_if_status:
            return None
        report.update(fields)
        _path(report_id).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


# ---------------- public API ----------------

def save(report: dict[str, Any]) -> None:
    (_file_save if BACKEND == "files" else _pg_save)(report)


def list_reports(limit: int = 200) -> list[dict[str, Any]]:
    return (_file_list if BACKEND == "files" else _pg_list)(limit)


def get(report_id: str) -> dict[str, Any]:
    _valid_id(report_id)
    return (_file_get if BACKEND == "files" else _pg_get)(report_id)


def update(report_id: str, **fields: Any) -> dict[str, Any]:
    _valid_id(report_id)
    result = (_file_update if BACKEND == "files" else _pg_update)(report_id, fields, None)
    if result is None:
        raise KeyError(report_id)
    return result


def transition(report_id: str, from_status: str, **fields: Any) -> dict[str, Any] | None:
    """Atomically apply `fields` only if the report is still in `from_status`; None if it wasn't."""
    _valid_id(report_id)
    return (_file_update if BACKEND == "files" else _pg_update)(report_id, fields, from_status)


# ---------------- CLI ----------------

def _import(folder: Path) -> None:
    files = sorted(folder.rglob("*.json"))
    for f in files:
        save(json.loads(f.read_text(encoding="utf-8")))
    print(f"Imported {len(files)} reports from {folder} into {BACKEND}")


def _stats() -> None:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT component, count(*) AS incidents,
                      count(*) FILTER (WHERE status = 'resolved') AS resolved,
                      round(avg(EXTRACT(EPOCH FROM ((data->>'resolved_at')::timestamptz - created_at))) / 60, 1)
                        AS avg_minutes_to_resolve
               FROM incidents GROUP BY component ORDER BY incidents DESC""").fetchall()
    print(f"{'component':<24}{'incidents':>10}{'resolved':>10}{'avg min to resolve':>20}")
    for c, n, r, m in rows:
        print(f"{c or '?':<24}{n:>10}{r:>10}{'' if m is None else m:>20}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["import"] and len(sys.argv) > 2:
        _import(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["stats"]:
        _stats()
    else:
        print(__doc__)
