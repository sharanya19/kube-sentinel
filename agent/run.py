"""One investigation end to end: load config, connect to MCP, run the graph, save the report."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

import warnings

# Lite models ignore temperature and the SDK warns on every call; that's noise for users.
warnings.filterwarnings("ignore", message=".*uses fixed sampling defaults.*")
warnings.filterwarnings("ignore", message=".*was never awaited.*", category=RuntimeWarning)

from .graph import investigate
from .models import ModelPool
from .mcp_tools import mcp_tools

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


def check_api_key() -> None:
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        raise SystemExit("No Gemini API key. Copy .env.example to .env and set GOOGLE_API_KEY "
                         "(free key: https://aistudio.google.com).")


async def run_investigation(alert: str, on_tool: Callable[[str, dict], None] | None = None,
                            source: str = "cli", save: bool = True, on_switch=None) -> dict:
    check_api_key()
    async with mcp_tools(read_only=True) as tools:
        result = await investigate(alert, tools, on_tool=on_tool, pool=ModelPool(on_switch=on_switch))

    _check_runbook_citation(result["report"])
    # Record the target deployment's state now, so the fix can be checked against it before it runs.
    from . import guard
    result["baseline"] = guard.snapshot(result["report"].suggested_fix.model_dump())
    result["id"] = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    result["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["alert"] = alert
    result["source"] = source
    result["model"] = ", ".join(result["models"])
    # Nothing wrong -> nothing to approve; the report is kept for the record.
    no_incident = result["report"].category == "no-incident"
    result["status"] = "no_incident" if no_incident else "pending_approval"
    if save:
        from . import store
        store.save(to_json(result))
    return result


def _check_runbook_citation(report) -> None:
    """Drop a runbook citation that doesn't point at a real runbook file (no made-up sources)."""
    if not report.runbook:
        return
    path = report.runbook.split("#", 1)[0].strip()
    if not (path.startswith("runbooks/") and path.endswith(".md") and (ROOT / path).is_file()):
        report.runbook = None


def to_json(result: dict) -> dict:
    out = {k: v for k, v in result.items() if k != "report"}
    out["report"] = result["report"].model_dump()
    out["report"]["suggested_fix"]["command"] = result["report"].suggested_fix.command
    return out
