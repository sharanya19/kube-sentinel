"""Approval UI: a small local web app over the agent's incident reports.

    python -m ui              # http://127.0.0.1:8000
    python -m ui --watch      # also watch Alertmanager and investigate new alerts automatically

The page lists reports; for each one a human can preview the fix (dry run), Approve it (the fix
runs through the MCP server's allowlisted actions, then recovery is verified) or Reject it.
The server listens on 127.0.0.1 only: it can change the cluster, so it is not exposed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import guard, store
from agent import watch as watcher
from agent.models import explain_error
from agent.run import run_investigation, to_json
from mcp_server import k8s

from . import fixer

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("kube-sentinel.ui")

# Investigations in progress, shown live on the page: {temp_id: {...}}
running: dict[str, dict[str, Any]] = {}
WATCH = {"enabled": False, "last_check": None, "error": None}
_busy: set[str] = set()  # report ids with a fix in flight (prevents double-approve)


# ---------------- investigations ----------------

async def start_investigation(alert: str, source: str) -> str:
    temp_id = "run-" + uuid.uuid4().hex[:8]
    entry = {"id": temp_id, "alert": alert, "source": source, "started_at": store.now(),
             "trace": [], "model_switches": [], "error": None}
    running[temp_id] = entry

    async def work() -> None:
        try:
            result = await run_investigation(
                alert, source=source, save=False,
                on_tool=lambda n, a: entry["trace"].append({"tool": n, "args": a}),
                on_switch=lambda f, n, why: entry["model_switches"].append(f"{f}: {why}"))
            store.save(to_json(result))
            running.pop(temp_id, None)
        except BaseException as e:  # keep the failed entry visible with a readable reason
            log.exception("investigation failed")
            entry["error"] = explain_error(e)
            entry["finished_at"] = store.now()

    asyncio.create_task(work())
    return temp_id


def _already_investigated(alerts: list[dict]) -> bool:
    """True if an alert-triggered incident was created after these alerts started.

    The watcher's memory is lost when the pod restarts; Postgres isn't. This stops a restart
    (or a second replica) from investigating the same incident again and burning LLM quota.
    """
    starts = [datetime.fromisoformat(a["since"].replace("Z", "+00:00")) for a in alerts if a.get("since")]
    if not starts:
        return False
    first = min(starts)
    for r in store.list_reports(limit=20):
        if r.get("source") == "alertmanager" and datetime.fromisoformat(r["created_at"]) >= first:
            return True
    return False


async def watch_loop() -> None:
    """Same logic as `python -m agent.watch`, but investigations show up live on the page."""
    seen: set[str] = set()
    while True:
        try:
            alerts = await asyncio.to_thread(watcher.shop_alerts)
            WATCH.update(last_check=store.now(), error=None)
            current = {a["fingerprint"] for a in alerts}
            busy = any(not r.get("error") for r in running.values())
            if current - seen and not busy:
                await asyncio.sleep(watcher.SETTLE_SECONDS)  # let related alerts arrive
                alerts = await asyncio.to_thread(watcher.shop_alerts) or alerts
                if not await asyncio.to_thread(_already_investigated, alerts):
                    await start_investigation(watcher.alert_text(alerts), "alertmanager")
                seen |= {a["fingerprint"] for a in alerts}
            elif not current:
                seen.clear()
        except Exception as e:
            WATCH["error"] = str(e)[:200]
        await asyncio.sleep(watcher.POLL_SECONDS)


EXPIRE_MINUTES = int(os.environ.get("KUBE_SENTINEL_EXPIRE_MINUTES", "60"))


def expire_old_reports() -> list[str]:
    """Waiting reports older than EXPIRE_MINUTES whose problem is gone, or whose cluster changed,
    become 'expired'. Kept for history; they just can't be approved any more."""
    expired = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EXPIRE_MINUTES)
    for r in store.list_reports(limit=200):
        if r.get("status") != "pending_approval" or datetime.fromisoformat(r["created_at"]) > cutoff:
            continue
        verdict, reason = guard.assess(r)
        if verdict != "valid":
            if store.transition(r["id"], "pending_approval", status="expired", check={
                    "verdict": verdict, "reason": reason, "at": store.now(), "on": "expiry"}):
                expired.append(r["id"])
    return expired


async def expire_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(expire_old_reports)
        except Exception:
            log.exception("expiry check failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(expire_loop())]
    if os.environ.get("KUBE_SENTINEL_UI_WATCH") == "1":
        WATCH["enabled"] = True
        tasks.append(asyncio.create_task(watch_loop()))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Kube-Sentinel", lifespan=lifespan)


@app.middleware("http")
async def revalidate_page_files(request, call_next):
    """Make browsers re-check the page's files (ETag) so an updated UI shows up without a hard refresh."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith(("/static/", "/runbooks/")):
        response.headers["Cache-Control"] = "no-cache"
    return response

app.mount("/static", StaticFiles(directory=STATIC), name="static")
# Runbooks cited in reports open as plain Markdown.
app.mount("/runbooks", StaticFiles(directory=STATIC.parent.parent / "runbooks"), name="runbooks")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# ---------------- API ----------------

def _cluster_health() -> dict[str, Any]:
    try:
        pods = k8s.list_pods("default", only_unhealthy=True)
        deps = [d for d in k8s.list_deployments("default") if d["state"] != "healthy"]
        return {"ok": not pods and not deps, "unhealthy_pods": [p["name"] for p in pods],
                "unhealthy_deployments": [f"{d['name']}: {d['state']}" for d in deps]}
    except Exception as e:
        return {"ok": None, "error": f"Can't reach the cluster: {str(e)[:150]}"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness check for Kubernetes: the web server is up."""
    return {"status": "ok"}


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return {"cluster": await asyncio.to_thread(_cluster_health),
            "running": list(running.values()), "watch": WATCH}


@app.get("/api/reports")
def reports() -> list[dict[str, Any]]:
    return store.list_reports()


def _get(report_id: str) -> dict[str, Any]:
    try:
        return store.get(report_id)
    except KeyError:
        raise HTTPException(404, "Report not found")


@app.get("/api/reports/{report_id}")
def report(report_id: str) -> dict[str, Any]:
    return _get(report_id)


@app.post("/api/reports/{report_id}/preview")
async def preview(report_id: str) -> dict[str, Any]:
    """Dry run: what the fix would do, without changing anything."""
    fix = _get(report_id)["report"]["suggested_fix"]
    try:
        return await fixer.call_action(fix, dry_run=True)
    except Exception as e:
        raise HTTPException(400, str(e))


class Decision(BaseModel):
    reason: str | None = None


@app.post("/api/reports/{report_id}/approve")
async def approve(report_id: str) -> dict[str, Any]:
    rep = _get(report_id)
    fix = rep["report"]["suggested_fix"]
    if fix.get("action") not in fixer.ACTIONS:
        raise HTTPException(400, "This report has no automatic fix to approve")
    if rep.get("status") != "pending_approval":
        raise HTTPException(409, f"Report is '{rep.get('status')}', not waiting for approval")

    # The report is a snapshot. Before running anything, check the cluster still matches it,
    # then dry-run the action. Either failing blocks the fix with nothing changed.
    verdict, reason = await asyncio.to_thread(guard.assess, rep)
    if verdict != "valid":
        status = "expired" if verdict == "gone" else "blocked"
        store.transition(report_id, "pending_approval", status=status, check={"verdict": verdict,
                         "reason": reason, "at": store.now(), "on": "approve"})
        raise HTTPException(409, f"Not run, nothing was changed: {reason}")
    try:
        preview_result = await fixer.call_action(fix, dry_run=True)
    except Exception as e:
        store.transition(report_id, "pending_approval", status="blocked", check={
            "verdict": "dry_run_failed", "reason": str(e)[:300], "at": store.now(), "on": "approve"})
        raise HTTPException(409, f"Not run, nothing was changed: the dry run failed ({str(e)[:200]})")

    # Atomic: only one click (from any tab or replica) can move it out of pending_approval.
    rep = store.transition(report_id, "pending_approval", status="fixing",
                           decision={"decision": "approved", "at": store.now()},
                           check={"verdict": "valid", "reason": reason, "at": store.now(), "on": "approve"},
                           dry_run=preview_result)
    if rep is None:
        raise HTTPException(409, f"Report is '{_get(report_id).get('status')}', not waiting for approval")
    _busy.add(report_id)

    async def work() -> None:
        try:
            result = await fixer.call_action(fix, dry_run=False)
            store.update(report_id, fix_result=result, fix_applied=True)
            check = await fixer.verify(fix["deployment"])
            store.update(report_id, verification=check, resolved_at=store.now(),
                         status="resolved" if check["healthy"] else "fix_failed")
        except Exception as e:
            # The action raised before or while patching: report it as not applied.
            store.update(report_id, status="fix_failed", fix_error=str(e)[:500], fix_applied=False)
        finally:
            _busy.discard(report_id)

    asyncio.create_task(work())
    return rep


@app.post("/api/reports/{report_id}/reject")
def reject(report_id: str, body: Decision) -> dict[str, Any]:
    _get(report_id)
    rep = store.transition(report_id, "pending_approval", status="rejected",
                           decision={"decision": "rejected", "at": store.now(), "reason": body.reason})
    if rep is None:
        raise HTTPException(409, f"Report is '{_get(report_id).get('status')}', not waiting for approval")
    return rep


@app.post("/api/reports/{report_id}/reinvestigate")
async def reinvestigate(report_id: str) -> dict[str, str]:
    """Start a fresh investigation from the same alert (for blocked or expired reports)."""
    rep = _get(report_id)
    if any(not r.get("error") for r in running.values()):
        raise HTTPException(409, "An investigation is already running")
    return {"id": await start_investigation(rep["alert"], "reinvestigation")}


class InvestigateRequest(BaseModel):
    alert: str


@app.post("/api/investigate")
async def investigate(body: InvestigateRequest) -> dict[str, str]:
    if any(not r.get("error") for r in running.values()):
        raise HTTPException(409, "An investigation is already running")
    return {"id": await start_investigation(body.alert.strip() or watcher.alert_text([]), "ui")}


@app.delete("/api/running/{temp_id}")
def dismiss_failed(temp_id: str) -> dict[str, bool]:
    entry = running.get(temp_id)
    if entry and entry.get("error"):
        running.pop(temp_id)
    return {"ok": True}
