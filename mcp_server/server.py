"""Kube-Sentinel MCP server: the tools an AI SRE agent uses to investigate a Kubernetes cluster.

Run it:  python -m mcp_server            (stdio transport, for MCP clients / the agent)
         python -m mcp_server --http     (streamable HTTP on http://127.0.0.1:8765/mcp)
"""

from __future__ import annotations

import functools
import json
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from kubernetes.client.exceptions import ApiException
from mcp.types import ToolAnnotations

from . import actions, k8s
from . import observability as obs

INSTRUCTIONS = """\
Tools for investigating incidents in a Kubernetes cluster running the Online Boutique shop
(namespace "default"; monitoring stack in "monitoring").

Suggested investigation order:
1. cluster_overview: what's unhealthy right now (pods, deployments, services, alerts, warnings).
2. Narrow down: describe_pod / describe_deployment / rollout_history on suspicious components.
   Check rollout_history whenever a deployment changed recently: most incidents follow a change.
3. Evidence: get_events, get_pod_logs (previous=true for crashed containers), error_log_summary, query_logs.
4. Metrics: resource_usage, query_metrics / query_metrics_range for trends.
5. Runbooks: once you suspect a cause, search_runbooks for the team's confirmation steps and fix.

Notes:
- A missing pod is as important as a crashing one: a deployment scaled to 0 or a service with
  0 ready endpoints means nothing is serving it.
- Errors usually show up first in the CALLER's logs (e.g. frontend), not in the broken service.
- Some errors are normal background noise in this shop: paymentservice rejecting "visa_electron"
  cards (the load generator uses it on purpose) and loadgenerator/frontend reporting those failed
  checkouts. frontend's SHOPPING_ASSISTANT_SERVICE_ADDR points at an optional service that isn't
  deployed; that is expected.
- It's fine to conclude that nothing is wrong.
"""

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer(name="kube-sentinel", instructions=INSTRUCTIONS, version="0.1.0")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)


def explain(fn):
    """Return the real reason to the caller (e.g. "pod not found") instead of a generic tool error."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except Exception as e:
            reason = str(e)
            if isinstance(e, ApiException):  # Kubernetes errors carry a JSON body with the real message
                try:
                    reason = json.loads(e.body)["message"]
                except Exception:
                    reason = getattr(e, "reason", None) or reason
            raise ToolError(f"{fn.__name__} failed: {reason}") from e
    return wrapper


# ---------------- triage ----------------

@mcp.tool(annotations=READ)

@explain
def cluster_overview(namespace: str = "default") -> dict[str, Any]:
    """Start here. One-call health check: unhealthy pods, deployments that aren't fully healthy,
    services with no ready endpoints, firing alerts, and warning events from the last 15 minutes."""
    out: dict[str, Any] = {
        "namespace": namespace,
        "unhealthy_pods": k8s.list_pods(namespace, only_unhealthy=True),
        "unhealthy_deployments": [d for d in k8s.list_deployments(namespace) if d["state"] != "healthy"],
        "services_without_endpoints": [s["name"] for s in k8s.list_services(namespace) if s["ready_endpoints"] == 0],
        "warning_events_15m": k8s.get_events(namespace, since_minutes=15, warnings_only=True, limit=15),
    }
    try:
        out["firing_alerts"] = obs.firing_alerts()
    except Exception as e:  # Prometheus being down shouldn't hide the cluster view
        out["firing_alerts_error"] = str(e)
    out["all_clear"] = not any(out[k] for k in
                              ("unhealthy_pods", "unhealthy_deployments", "services_without_endpoints",
                               "warning_events_15m")) and not out.get("firing_alerts")
    return out


# ---------------- Kubernetes ----------------

@mcp.tool(annotations=READ)

@explain
def list_pods(namespace: str = "default", only_unhealthy: bool = False) -> list[dict]:
    """List pods with ready count, status (e.g. CrashLoopBackOff, ImagePullBackOff), restarts,
    and the last termination reason (e.g. OOMKilled, exit code 137)."""
    return k8s.list_pods(namespace, only_unhealthy)


@mcp.tool(annotations=READ)


@explain
def describe_pod(name: str, namespace: str = "default") -> dict[str, Any]:
    """Full detail for one pod: container image, env, resource requests/limits, liveness/readiness
    probes, current and last container state, conditions, and the pod's recent events."""
    return k8s.describe_pod(name, namespace)


@mcp.tool(annotations=READ)


@explain
def list_deployments(namespace: str = "default") -> list[dict]:
    """List deployments with desired/ready/updated/available replicas, image, and a plain-English
    state such as 'healthy', 'scaled to zero' or 'rollout in progress / stuck'."""
    return k8s.list_deployments(namespace)


@mcp.tool(annotations=READ)


@explain
def describe_deployment(name: str, namespace: str = "default") -> dict[str, Any]:
    """Full detail for one deployment: replicas, rollout state, current revision, container
    image / env / resources / probes, and deployment conditions."""
    return k8s.describe_deployment(name, namespace)


@mcp.tool(annotations=READ)


@explain
def rollout_history(name: str, namespace: str = "default") -> dict[str, Any]:
    """Recent revisions of a deployment (newest first, with creation time) and exactly what changed
    in the latest revision: image, env vars, resource limits, probes. Use it to find bad deploys."""
    return k8s.rollout_history(name, namespace)


@mcp.tool(annotations=READ)


@explain
def list_services(namespace: str = "default") -> list[dict]:
    """List services with ports and how many ready endpoints (backing pods) each has.
    ready_endpoints = 0 means calls to that service will fail."""
    return k8s.list_services(namespace)


@mcp.tool(annotations=READ)


@explain
def get_events(namespace: str = "default", object_name: str | None = None, since_minutes: int = 60,
               warnings_only: bool = False) -> list[dict]:
    """Kubernetes events, newest first: scaling, pod kills, failed image pulls, failed probes,
    OOM kills, scheduling problems. Filter by object name (substring, e.g. 'redis-cart')."""
    return k8s.get_events(namespace, object_name, since_minutes, warnings_only)


@mcp.tool(annotations=READ)


@explain
def get_pod_logs(name: str, namespace: str = "default", tail_lines: int = 50, previous: bool = False,
                 contains: str | None = None) -> list[dict]:
    """Recent log lines straight from Kubernetes for a pod, or for all pods of an app
    (e.g. name='cartservice'). previous=true shows the last crashed container's logs.
    contains filters lines by a case-insensitive substring."""
    return k8s.get_pod_logs(name, namespace, tail_lines, previous, contains)


# ---------------- Logs (Loki) ----------------

@mcp.tool(annotations=READ)

@explain
def error_log_summary(namespace: str = "default", since_minutes: int = 15) -> list[dict]:
    """Which apps are logging errors (error / exception / refused / timeout ...), how many lines,
    and a few example lines each. Good for finding where failures surface."""
    return obs.error_log_summary(namespace, since_minutes)


@mcp.tool(annotations=READ)


@explain
def query_logs(logql: str, since_minutes: int = 30, limit: int = 50) -> dict[str, Any]:
    """Search logs in Loki with LogQL. Labels: namespace, app, pod, container; Kubernetes events
    are under {job=~".*kubernetes-events.*"}. Examples:
    {app="cartservice"} |= "Redis"      {namespace="default"} |~ "(?i)timeout" """
    return obs.query_logs(logql, since_minutes, limit)


# ---------------- Metrics (Prometheus) ----------------

@mcp.tool(annotations=READ)

@explain
def resource_usage(namespace: str = "default") -> list[dict]:
    """Per-pod CPU (cores) and memory (MiB) next to their limits, CPU throttling %,
    and restarts in the last 15 minutes."""
    return obs.resource_usage(namespace)


@mcp.tool(annotations=READ)


@explain
def query_metrics(promql: str) -> dict[str, Any]:
    """Run an instant PromQL query. Metrics are per pod (use `sum by (pod)`), e.g.
    kube_deployment_status_replicas_available{namespace="default"}
    sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="default"}[2m]))"""
    return obs.query_metrics(promql)


@mcp.tool(annotations=READ)


@explain
def query_metrics_range(promql: str, minutes: int = 30, step_seconds: int = 60) -> dict[str, Any]:
    """Run a PromQL query over the last N minutes and get first/last/min/max plus recent points,
    to see WHEN something changed (e.g. replicas dropping from 1 to 0)."""
    return obs.query_metrics_range(promql, minutes, step_seconds)


@mcp.tool(annotations=READ)


@explain
def get_firing_alerts() -> list[dict]:
    """Alerts currently firing in Prometheus (the always-on Watchdog heartbeat is excluded)."""
    return obs.firing_alerts()


# ---------------- Runbooks (RAG) ----------------

@mcp.tool(annotations=READ)
@explain
def search_runbooks(query: str, k: int = 3, category: str | None = None,
                    component: str | None = None) -> list[dict]:
    """Search the team's SRE runbooks (hybrid: semantic + keyword). Describe the symptom or suspected
    cause, e.g. 'pod OOMKilled exit code 137 after memory limit change'. Returns the best runbooks with
    the matching section, the runbook's Fix section, and a citation to put in the report.
    Optional filters: category (e.g. 'oom-killed') and component (e.g. 'redis-cart')."""
    from rag.search import search  # lazy: loads the embedding model on first use
    hits = search(query, k=max(1, min(k, 5)), category=category, component=component)
    for h in hits:
        h["excerpt"] = h["excerpt"][:900]
        h["fix"] = h["fix"][:900]
    return hits


# ---------------- Actions (opt-in) ----------------

if actions.enabled():
    @mcp.tool(annotations=WRITE)
    @explain
    def scale_deployment(name: str, replicas: int, namespace: str = "default", dry_run: bool = True) -> dict:
        """CHANGES THE CLUSTER. Scale a deployment (0-5 replicas). Only call after human approval.
        dry_run=true (default) only describes what would happen."""
        return actions.scale_deployment(name, replicas, namespace, dry_run)

    @mcp.tool(annotations=WRITE)

    @explain
    def rollout_restart(name: str, namespace: str = "default", dry_run: bool = True) -> dict:
        """CHANGES THE CLUSTER. Restart all pods of a deployment. Only call after human approval."""
        return actions.rollout_restart(name, namespace, dry_run)

    @mcp.tool(annotations=WRITE)

    @explain
    def rollout_undo(name: str, namespace: str = "default", dry_run: bool = True) -> dict:
        """CHANGES THE CLUSTER. Roll a deployment back to its previous revision (like
        `kubectl rollout undo`). Only call after human approval."""
        return actions.rollout_undo(name, namespace, dry_run)


def main() -> None:
    if "--http" in sys.argv:
        mcp.run("streamable-http", host="127.0.0.1", port=8765)
    else:
        mcp.run()
