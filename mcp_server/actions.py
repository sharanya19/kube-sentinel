"""The only cluster changes Kube-Sentinel is allowed to make.

These are exposed as MCP tools ONLY when KUBE_SENTINEL_ALLOW_ACTIONS=true, and
only for namespaces in KUBE_SENTINEL_ACTION_NAMESPACES (default: "default").
The agent never calls them on its own: the approval UI calls them after a human clicks Approve.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from . import k8s

ALLOWED_NAMESPACES = {n.strip() for n in os.getenv("KUBE_SENTINEL_ACTION_NAMESPACES", "default").split(",")}
MAX_REPLICAS = 5


def enabled() -> bool:
    return os.getenv("KUBE_SENTINEL_ALLOW_ACTIONS", "").lower() in ("1", "true", "yes")


def _check(namespace: str) -> None:
    if namespace not in ALLOWED_NAMESPACES:
        raise PermissionError(f"Actions are not allowed in namespace '{namespace}'. Allowed: {sorted(ALLOWED_NAMESPACES)}")


def scale_deployment(name: str, replicas: int, namespace: str = "default", dry_run: bool = True) -> dict:
    _check(namespace)
    if not 0 <= replicas <= MAX_REPLICAS:
        raise ValueError(f"replicas must be between 0 and {MAX_REPLICAS}")
    before = k8s.apps().read_namespaced_deployment(name, namespace).spec.replicas
    if not dry_run:
        k8s.apps().patch_namespaced_deployment_scale(name, namespace, {"spec": {"replicas": replicas}})
    return {"action": "scale", "deployment": name, "from": before, "to": replicas, "dry_run": dry_run,
            "command": f"kubectl scale deployment/{name} -n {namespace} --replicas={replicas}"}


def rollout_restart(name: str, namespace: str = "default", dry_run: bool = True) -> dict:
    _check(namespace)
    k8s.apps().read_namespaced_deployment(name, namespace)  # fails if it doesn't exist
    if not dry_run:
        stamp = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": stamp}}}}}
        k8s.apps().patch_namespaced_deployment(name, namespace, body)
    return {"action": "rollout_restart", "deployment": name, "dry_run": dry_run,
            "command": f"kubectl rollout restart deployment/{name} -n {namespace}"}


def rollout_undo(name: str, namespace: str = "default", dry_run: bool = True) -> dict:
    """Roll back to the previous revision, the same thing `kubectl rollout undo` does."""
    _check(namespace)
    rs_list = k8s._owned_replicasets(name, namespace)
    if len(rs_list) < 2:
        raise ValueError(f"{name} has no previous revision to roll back to")
    current, previous = rs_list[-1], rs_list[-2]
    template = k8s._load().sanitize_for_serialization(previous.spec.template)
    template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
    history = k8s.rollout_history(name, namespace)
    if not dry_run:
        k8s.apps().patch_namespaced_deployment(
            name, namespace, [{"op": "replace", "path": "/spec/template", "value": template}])
    rev = lambda r: (r.metadata.annotations or {}).get("deployment.kubernetes.io/revision")  # noqa: E731
    return {"action": "rollout_undo", "deployment": name, "from_revision": rev(current),
            "to_revision": rev(previous), "reverts": history["latest_revision_changes"], "dry_run": dry_run,
            "command": f"kubectl rollout undo deployment/{name} -n {namespace}"}
