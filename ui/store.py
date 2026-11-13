"""Incident reports on disk (reports/<id>.json), shared by the agent and the approval UI.

Status flow:
    pending_approval --Approve--> fixing --> resolved | fix_failed
                     --Reject---> rejected
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.run import REPORTS_DIR

_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(report_id: str) -> Path:
    if not report_id.replace("-", "").isalnum():  # ids look like 20261112-133918-9cf7f0
        raise KeyError(report_id)
    return REPORTS_DIR / f"{report_id}.json"


def list_reports() -> list[dict[str, Any]]:
    if not REPORTS_DIR.exists():
        return []
    out = []
    for p in REPORTS_DIR.glob("*.json"):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda r: r.get("created_at", ""), reverse=True)


def get(report_id: str) -> dict[str, Any]:
    p = _path(report_id)
    if not p.exists():
        raise KeyError(report_id)
    return json.loads(p.read_text(encoding="utf-8"))


def save(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(exist_ok=True)
    with _lock:
        _path(report["id"]).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def update(report_id: str, **fields: Any) -> dict[str, Any]:
    with _lock:
        report = get(report_id)
        report.update(fields)
        _path(report_id).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
