"""Make sure a suggested fix still fits the cluster before anyone can run it.

A report is a snapshot: the fix was right when the agent wrote it. If the deployment changed
afterwards (someone fixed it, rolled forward, restored it), running the old fix can be useless or
harmful: "roll back one revision" after a newer fix would re-deploy the broken version. So:

- snapshot():  when a report is written, record the target deployment's state (a fingerprint of its
               pod template, its revision and replica count).
- assess():    before approval (and periodically for old reports), compare that with the live cluster.

    valid    -> nothing changed since the investigation; the fix may run
    changed  -> the deployment changed since the report; block the fix, re-investigate
    gone     -> the problem is no longer there; the report can expire
    unknown  -> no snapshot (report written before this check existed); treat as not safe to run
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from mcp_server import k8s
from mcp_server import observability as obs

NAMESPACE = "default"


def _template_hash(dep) -> str:
    template = k8s._load().sanitize_for_serialization(dep.spec.template)
    return hashlib.sha256(json.dumps(template, sort_keys=True).encode()).hexdigest()[:16]


def _state(name: str) -> dict[str, Any] | None:
    try:
        dep = k8s.apps().read_namespaced_deployment(name, NAMESPACE)
    except Exception:
        return None
    return {
        "deployment": name,
        "revision": (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
        "template_hash": _template_hash(dep),
        "replicas": dep.spec.replicas or 0,
        "available": dep.status.available_replicas or 0,
    }


def snapshot(fix: dict[str, Any]) -> dict[str, Any] | None:
    """State of the fix's target deployment right now (None if the fix targets no deployment)."""
    name = fix.get("deployment")
    if not name or fix.get("action") in (None, "none"):
        return None
    state = _state(name)
    if state:
        state["taken_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return state


def _shop_alerts_firing() -> bool:
    try:
        return any(a.get("namespace") == NAMESPACE for a in obs.firing_alerts())
    except Exception:
        return True  # can't tell: don't expire anything on a monitoring hiccup


def assess(report: dict[str, Any]) -> tuple[str, str]:
    """(verdict, human-readable reason) for whether this report's fix still fits the cluster."""
    fix = report["report"]["suggested_fix"]
    action, name = fix.get("action"), fix.get("deployment")

    if action in (None, "none") or not name:
        if _shop_alerts_firing():
            return "valid", "Alerts are still firing."
        return "gone", "No shop alerts are firing any more."

    base = report.get("baseline")
    now = _state(name)
    if now is None:
        return "changed", f"Deployment {name} no longer exists."
    if not base:
        return "unknown", ("This report was written before fixes were checked against the cluster, "
                           "so it can't be confirmed the fix is still safe.")

    if action == "scale_deployment" and base["replicas"] == 0 and now["replicas"] > 0:
        return "gone", f"{name} is already scaled back to {now['replicas']} replica(s)."
    if now["template_hash"] != base["template_hash"]:
        return "changed", (f"{name} was redeployed after this report (revision {base['revision']} -> "
                           f"{now['revision']}), so this fix may no longer be safe.")
    if now["replicas"] != base["replicas"]:
        return "changed", f"{name} was scaled from {base['replicas']} to {now['replicas']} after this report."
    return "valid", f"{name} is unchanged since the investigation (revision {now['revision']})."
