"""Read-only helpers for the Kubernetes API.

Every function returns small, plain dicts: compact enough for an LLM to read,
with the fields an SRE would actually look at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from kubernetes import client, config

MAX_LOG_LINE = 300
RECENT_RESTART_SECONDS = 15 * 60


@lru_cache(maxsize=1)
def _load() -> client.ApiClient:
    try:
        config.load_kube_config()
    except config.ConfigException:
        config.load_incluster_config()
    return client.ApiClient()


def core() -> client.CoreV1Api:
    return client.CoreV1Api(_load())


def apps() -> client.AppsV1Api:
    return client.AppsV1Api(_load())


def discovery() -> client.DiscoveryV1Api:
    return client.DiscoveryV1Api(_load())


def api_configuration() -> client.Configuration:
    _load()
    return client.Configuration.get_default_copy()


# ---------- small helpers ----------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(ts: datetime | None) -> str:
    if not ts:
        return "?"
    secs = int((_now() - ts).total_seconds())
    if secs < 120:
        return f"{secs}s"
    if secs < 7200:
        return f"{secs // 60}m"
    if secs < 172800:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _app_of(labels: dict | None, fallback: str) -> str:
    labels = labels or {}
    return labels.get("app") or labels.get("app.kubernetes.io/name") or fallback


# ---------- pods ----------

def _pod_summary(p: client.V1Pod) -> dict[str, Any]:
    statuses = p.status.container_statuses or []
    ready = sum(1 for c in statuses if c.ready)
    restarts = sum(c.restart_count for c in statuses)
    state = p.status.phase
    reason_detail = None
    last_termination = None
    recently_restarted = False
    for c in statuses:
        if c.state and c.state.waiting:
            state = c.state.waiting.reason or "Waiting"
            reason_detail = (c.state.waiting.message or "")[:200] or None
        elif c.state and c.state.terminated:
            state = c.state.terminated.reason or "Terminated"
        if c.last_state and c.last_state.terminated:
            t = c.last_state.terminated
            last_termination = {
                "reason": t.reason,
                "exit_code": t.exit_code,
                "finished": _age(t.finished_at) + " ago",
            }
            if t.finished_at and (_now() - t.finished_at).total_seconds() < RECENT_RESTART_SECONDS:
                recently_restarted = True
    if p.metadata.deletion_timestamp:
        state = "Terminating"
    # A pod that looks Running right now but restarted minutes ago is still a problem (restart loop).
    healthy = (state in ("Running", "Succeeded", "Terminating") and ready == len(statuses)
               and not recently_restarted)
    out = {
        "name": p.metadata.name,
        "app": _app_of(p.metadata.labels, p.metadata.name),
        "ready": f"{ready}/{len(statuses)}",
        "status": state,
        "restarts": restarts,
        "age": _age(p.metadata.creation_timestamp),
        "healthy": healthy,
    }
    if reason_detail:
        out["status_message"] = reason_detail
    if last_termination:
        out["last_termination"] = last_termination
    return out


def list_pods(namespace: str = "default", only_unhealthy: bool = False) -> list[dict]:
    pods = core().list_namespaced_pod(namespace).items
    rows = [_pod_summary(p) for p in sorted(pods, key=lambda p: p.metadata.name)]
    return [r for r in rows if not r["healthy"]] if only_unhealthy else rows


def _container_spec(c: client.V1Container) -> dict[str, Any]:
    def probe(pr):
        if not pr:
            return None
        if pr.grpc:
            target = f"grpc port {pr.grpc.port}"
        elif pr.http_get:
            target = f"http {pr.http_get.path} port {pr.http_get.port}"
        elif pr.tcp_socket:
            target = f"tcp port {pr.tcp_socket.port}"
        elif pr._exec:
            target = "exec " + " ".join(pr._exec.command or [])
        else:
            target = "?"
        return {"target": target, "period_s": pr.period_seconds,
                "initial_delay_s": pr.initial_delay_seconds, "failure_threshold": pr.failure_threshold}

    res = c.resources
    return {
        "name": c.name,
        "image": c.image,
        "ports": [p.container_port for p in (c.ports or [])],
        "env": {e.name: e.value for e in (c.env or []) if e.value is not None},
        "resources": {"requests": (res.requests if res else None) or {},
                      "limits": (res.limits if res else None) or {}},
        "liveness_probe": probe(c.liveness_probe),
        "readiness_probe": probe(c.readiness_probe),
    }


def _not_found(name: str, namespace: str, pods: list) -> ValueError:
    apps = sorted({(p.metadata.labels or {}).get("app") for p in pods} - {None})
    close = [p.metadata.name for p in pods if p.metadata.name.startswith(name.split("-")[0])][:5]
    hint = f" Pods with a similar name: {close}." if close else ""
    return ValueError(f"No pod or app named '{name}' in namespace '{namespace}'.{hint} Apps: {apps}")


def _resolve_pod(name: str, namespace: str) -> tuple[client.V1Pod, list[str]]:
    """A pod by exact name, or the most relevant pod of an app (unhealthy first, then newest)."""
    try:
        return core().read_namespaced_pod(name, namespace), []
    except client.ApiException as e:
        if e.status != 404:
            raise
    pods = core().list_namespaced_pod(namespace).items
    matches = [p for p in pods if (p.metadata.labels or {}).get("app") == name]
    if not matches:
        raise _not_found(name, namespace, pods)
    matches.sort(key=lambda p: (_pod_summary(p)["healthy"], -p.metadata.creation_timestamp.timestamp()))
    return matches[0], [p.metadata.name for p in matches[1:]]


def describe_pod(name: str, namespace: str = "default") -> dict[str, Any]:
    """Full detail for a pod, given its exact name or its app name (e.g. 'frontend')."""
    p, others = _resolve_pod(name, namespace)
    name = p.metadata.name
    out = _pod_summary(p)
    if others:
        out["other_pods_of_app"] = others
    out["node"] = p.spec.node_name
    out["containers"] = [_container_spec(c) for c in p.spec.containers]
    out["container_states"] = []
    for c in p.status.container_statuses or []:
        st = c.state.to_dict() if c.state else {}
        last = c.last_state.to_dict() if c.last_state else {}
        out["container_states"].append({
            "name": c.name, "ready": c.ready, "restarts": c.restart_count,
            "state": {k: v for k, v in st.items() if v},
            "last_state": {k: v for k, v in last.items() if v},
        })
    out["conditions"] = [
        {"type": c.type, "status": c.status, "reason": c.reason}
        for c in (p.status.conditions or [])
    ]
    out["events"] = get_events(namespace, object_name=name, since_minutes=120)
    return out


# ---------- deployments ----------

def _rollout_state(d: client.V1Deployment) -> str:
    s, spec = d.status, d.spec
    desired = spec.replicas or 0
    if desired == 0:
        return "scaled to zero"
    for c in s.conditions or []:
        if c.type == "Progressing" and c.reason == "ProgressDeadlineExceeded":
            return "rollout stuck (progress deadline exceeded)"
    total, unavailable = s.replicas or 0, s.unavailable_replicas or 0
    if (s.updated_replicas or 0) < desired or (total > desired and unavailable > 0):
        return "rollout in progress / stuck: new pods not becoming ready (old pods still serving)"
    if (s.available_replicas or 0) < desired:
        return "degraded: not all replicas available"
    if total > desired:
        return "rollout finishing: old pods terminating"
    return "healthy"


def list_deployments(namespace: str = "default") -> list[dict]:
    rows = []
    for d in sorted(apps().list_namespaced_deployment(namespace).items, key=lambda d: d.metadata.name):
        s = d.status
        rows.append({
            "name": d.metadata.name,
            "desired": d.spec.replicas,
            "ready": s.ready_replicas or 0,
            "updated": s.updated_replicas or 0,
            "available": s.available_replicas or 0,
            "images": [c.image.rsplit("/", 1)[-1] for c in d.spec.template.spec.containers],
            "state": _rollout_state(d),
        })
    return rows


def describe_deployment(name: str, namespace: str = "default") -> dict[str, Any]:
    d = apps().read_namespaced_deployment(name, namespace)
    s = d.status
    return {
        "name": name,
        "desired": d.spec.replicas,
        "ready": s.ready_replicas or 0,
        "updated": s.updated_replicas or 0,
        "available": s.available_replicas or 0,
        "unavailable": s.unavailable_replicas or 0,
        "state": _rollout_state(d),
        "strategy": d.spec.strategy.type if d.spec.strategy else None,
        "revision": (d.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
        "containers": [_container_spec(c) for c in d.spec.template.spec.containers],
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:200]}
            for c in (s.conditions or [])
        ],
    }


def _owned_replicasets(name: str, namespace: str) -> list[client.V1ReplicaSet]:
    rs = apps().list_namespaced_replica_set(namespace).items
    owned = [r for r in rs if any(o.kind == "Deployment" and o.name == name
                                  for o in (r.metadata.owner_references or []))]
    return sorted(owned, key=lambda r: int((r.metadata.annotations or {}).get(
        "deployment.kubernetes.io/revision", "0")))


def _diff(old: dict, new: dict, prefix: str = "") -> list[str]:
    changes = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        path = f"{prefix}{key}"
        if isinstance(a, dict) and isinstance(b, dict):
            changes.extend(_diff(a, b, path + "."))
        else:
            changes.append(f"{path}: {a!r} -> {b!r}")
    return changes


def rollout_history(name: str, namespace: str = "default") -> dict[str, Any]:
    """Revisions of a deployment, newest first, plus what changed in the latest one."""
    rs_list = _owned_replicasets(name, namespace)
    # Kubernetes reuses an old ReplicaSet when a deployment returns to an identical template,
    # so its creation time can be much older than the rollout. "Scaled up" events show when
    # each revision was actually rolled out (events are kept for ~1 hour).
    scale_ups = [e for e in get_events(namespace, object_name=f"Deployment/{name}", since_minutes=24 * 60, limit=200)
                 if e["reason"] == "ScalingReplicaSet" and "Scaled up" in e["message"]]
    revisions = []
    for r in reversed(rs_list):
        rolled = next((e for e in scale_ups if f"replica set {r.metadata.name} " in e["message"]), None)
        revisions.append({
            "revision": int((r.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")),
            "replicaset": r.metadata.name,
            "rolled_out": (rolled["age"] + " ago") if rolled else "unknown (no recent scale-up event)",
            "rolled_out_at": rolled["time"] if rolled else None,
            "replicaset_created": _age(r.metadata.creation_timestamp) + " ago",
            "replicas": r.status.replicas or 0,
            "ready": r.status.ready_replicas or 0,
            "images": [c.image.rsplit("/", 1)[-1] for c in r.spec.template.spec.containers],
        })
    latest_change: list[str] = []
    if len(rs_list) >= 2:
        prev = {c.name: _container_spec(c) for c in rs_list[-2].spec.template.spec.containers}
        cur = {c.name: _container_spec(c) for c in rs_list[-1].spec.template.spec.containers}
        latest_change = _diff(prev, cur)
    out = {
        "deployment": name,
        "revisions": revisions[:5],
        "latest_revision_changes": latest_change or ["(no previous revision to compare, or no container changes)"],
    }
    if len(rs_list) >= 2:
        latest, prev_rs = rs_list[-1], rs_list[-2]
        # A rollback re-activates an OLD ReplicaSet: Kubernetes records its earlier revision numbers
        # in "revision-history", and it was created before the ReplicaSet it replaced.
        reused = bool((latest.metadata.annotations or {}).get("deployment.kubernetes.io/revision-history"))
        older = latest.metadata.creation_timestamp < prev_rs.metadata.creation_timestamp
        if reused or older:
            out["latest_revision_is_rollback"] = True
            out["note"] = ("The latest revision RETURNED to an earlier configuration that ran before "
                           "(a rollback / restore). That is usually a recovery, not the cause of an incident; "
                           "the 'changes' above undo a previous bad change.")
        else:
            out["latest_revision_is_rollback"] = False
    return out


# ---------- services ----------

def list_services(namespace: str = "default") -> list[dict]:
    """Services with how many ready backends (endpoints) each has. 0 ready = nothing can serve it."""
    slices = discovery().list_namespaced_endpoint_slice(namespace).items
    counts: dict[str, list[int]] = {}
    for sl in slices:
        svc = (sl.metadata.labels or {}).get("kubernetes.io/service-name")
        if not svc:
            continue
        ready = sum(1 for e in (sl.endpoints or []) if e.conditions and e.conditions.ready)
        total = len(sl.endpoints or [])
        c = counts.setdefault(svc, [0, 0])
        c[0] += ready
        c[1] += total
    rows = []
    for s in sorted(core().list_namespaced_service(namespace).items, key=lambda s: s.metadata.name):
        if s.metadata.name == "kubernetes":
            continue
        ready, total = counts.get(s.metadata.name, [0, 0])
        rows.append({
            "name": s.metadata.name,
            "type": s.spec.type,
            "ports": [f"{p.port}->{p.target_port}" for p in (s.spec.ports or [])],
            "selector": s.spec.selector,
            "ready_endpoints": ready,
            "not_ready_endpoints": total - ready,
        })
    return rows


# ---------- events ----------

def get_events(namespace: str = "default", object_name: str | None = None,
               since_minutes: int = 60, warnings_only: bool = False, limit: int = 40) -> list[dict]:
    cutoff = _now().timestamp() - since_minutes * 60
    rows = []
    for e in core().list_namespaced_event(namespace).items:
        ts = e.last_timestamp or e.event_time or e.metadata.creation_timestamp
        if not ts or ts.timestamp() < cutoff:
            continue
        obj = f"{e.involved_object.kind}/{e.involved_object.name}"
        if object_name and object_name not in obj:
            continue
        if warnings_only and e.type != "Warning":
            continue
        rows.append({
            "time": _iso(ts), "age": _age(ts), "type": e.type, "reason": e.reason,
            "object": obj, "message": (e.message or "")[:300], "count": e.count or 1,
        })
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows[:limit]


def mass_restart(namespace: str = "default", within_minutes: int = 60, window_seconds: int = 120) -> dict | None:
    """If several pods' last restart happened at the same moment, that's usually the node or cluster
    restarting (or a shared dependency), not a problem in each app. Returns a summary, or None."""
    cutoff = _now().timestamp() - within_minutes * 60
    ends = []
    for p in core().list_namespaced_pod(namespace).items:
        for c in p.status.container_statuses or []:
            t = c.last_state.terminated if c.last_state else None
            if t and t.finished_at and t.finished_at.timestamp() >= cutoff:
                ends.append((t.finished_at.timestamp(), p.metadata.name, t.reason, t.exit_code))
    ends.sort()
    best: list = []
    for i, (t0, *_rest) in enumerate(ends):
        group = [e for e in ends[i:] if e[0] - t0 <= window_seconds]
        if len(group) > len(best):
            best = group
    if len(best) < 3:
        return None
    reasons: dict[str, int] = {}
    for _, _, reason, code in best:
        key = f"{reason} (exit {code})"
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "pods_restarted_together": len(best),
        "at": _iso(datetime.fromtimestamp(best[0][0], timezone.utc)),
        "last_termination_reasons": reasons,
        "pods": [e[1] for e in best][:15],
        "hint": ("Many pods restarted within the same ~2 minutes: usually a node / cluster restart or a "
                 "shared dependency, not a fault in each app. Check node events. Exit 137/143 with reason "
                 "'Error' or 'Completed' means the container was stopped/killed, not OOMKilled."),
    }


def node_events(since_minutes: int = 60) -> list[dict]:
    """Recent events about the node itself (e.g. 'RegisteredNode', 'Starting', 'NodeNotReady')."""
    return [e for e in get_events("default", since_minutes=since_minutes, limit=200)
            if e["object"].startswith("Node/")][:10]


# ---------- logs ----------

def get_pod_logs(name: str, namespace: str = "default", tail_lines: int = 50,
                 previous: bool = False, contains: str | None = None) -> list[dict]:
    """Logs for a pod name, or for every pod of an app (e.g. 'cartservice')."""
    api = core()
    pods = [p for p in api.list_namespaced_pod(namespace).items
            if p.metadata.name == name or (p.metadata.labels or {}).get("app") == name]
    if not pods:
        raise _not_found(name, namespace, api.list_namespaced_pod(namespace).items)
    out = []
    for p in pods:
        try:
            text = api.read_namespaced_pod_log(p.metadata.name, namespace, tail_lines=tail_lines,
                                               previous=previous, _request_timeout=15)
        except client.ApiException as e:
            out.append({"pod": p.metadata.name, "error": f"{e.status} {e.reason}"})
            continue
        lines = [ln[:MAX_LOG_LINE] for ln in text.splitlines() if ln.strip()]
        if contains:
            lines = [ln for ln in lines if contains.lower() in ln.lower()]
        out.append({"pod": p.metadata.name, "previous_container": previous, "lines": lines})
    return out
