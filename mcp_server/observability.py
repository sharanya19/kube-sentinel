"""Read-only helpers for Prometheus (metrics) and Loki (logs).

By default both are reached through the Kubernetes API service proxy, so no
port-forward is needed. Set PROMETHEUS_URL / LOKI_URL to talk to them directly.
"""

from __future__ import annotations

import os
import ssl
import time
from functools import lru_cache
from typing import Any

import httpx

from . import k8s

MONITORING_NS = os.getenv("MONITORING_NAMESPACE", "monitoring")
PROM_SERVICE = os.getenv("PROMETHEUS_SERVICE", "kps-kube-prometheus-stack-prometheus:9090")
LOKI_SERVICE = os.getenv("LOKI_SERVICE", "loki:3100")
IGNORED_ALERTS = {"Watchdog", "InfoInhibitor"}
ERROR_PATTERN = r"(?i)(error|exception|fail|refused|timeout|timed out|panic|fatal|unavailable)"
# Lines that match ERROR_PATTERN but aren't errors (e.g. the load generator's stats table header).
NOISE_FILTER = '!~ "# reqs|# fails"'


@lru_cache(maxsize=2)
def _client(kind: str) -> httpx.Client:
    direct = os.getenv("PROMETHEUS_URL" if kind == "prom" else "LOKI_URL")
    if direct:
        return httpx.Client(base_url=direct.rstrip("/"), timeout=20)
    cfg = k8s.api_configuration()
    service = PROM_SERVICE if kind == "prom" else LOKI_SERVICE
    headers = {}
    if cfg.api_key.get("authorization"):
        prefix = cfg.api_key_prefix.get("authorization", "")
        headers["Authorization"] = f"{prefix} {cfg.api_key['authorization']}".strip()
    # Reuse the kubeconfig's TLS settings (CA + client certificate) for the API proxy.
    if cfg.verify_ssl:
        ctx = ssl.create_default_context(cafile=cfg.ssl_ca_cert or None)
    else:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if cfg.cert_file:
        ctx.load_cert_chain(cfg.cert_file, cfg.key_file)
    base = f"{cfg.host}/api/v1/namespaces/{MONITORING_NS}/services/{service}/proxy"
    return httpx.Client(base_url=base, headers=headers, verify=ctx, timeout=20)


def _get(kind: str, path: str, params: dict) -> dict:
    r = _client(kind).get(path, params=params)
    if r.status_code >= 400:
        raise ValueError(f"{kind} query failed ({r.status_code}): {r.text[:300]}")
    return r.json()


# ---------- Prometheus ----------

def _fmt(v: str) -> float | str:
    try:
        f = float(v)
        return round(f, 4)
    except ValueError:
        return v


def query_metrics(promql: str, max_series: int = 30) -> dict[str, Any]:
    data = _get("prom", "/api/v1/query", {"query": promql})["data"]
    result = data["result"]
    if data["resultType"] == "vector":
        series = [{"labels": {k: v for k, v in r["metric"].items()
                              if k not in ("endpoint", "instance", "job", "service", "prometheus", "uid")},
                   "value": _fmt(r["value"][1])} for r in result]
    else:
        series = result
    return {"query": promql, "series_count": len(result), "series": series[:max_series]}


def query_metrics_range(promql: str, minutes: int = 30, step_seconds: int = 60,
                        max_series: int = 10) -> dict[str, Any]:
    end = time.time()
    data = _get("prom", "/api/v1/query_range",
                {"query": promql, "start": end - minutes * 60, "end": end, "step": step_seconds})["data"]
    out = []
    for r in data["result"][:max_series]:
        pts = [_fmt(v[1]) for v in r["values"]]
        out.append({
            "labels": {k: v for k, v in r["metric"].items() if k not in ("endpoint", "instance", "job", "service")},
            "first": pts[0] if pts else None, "last": pts[-1] if pts else None,
            "min": min(pts) if pts else None, "max": max(pts) if pts else None,
            "points": pts[-15:],
        })
    return {"query": promql, "minutes": minutes, "step_seconds": step_seconds, "series": out}


def firing_alerts() -> list[dict]:
    alerts = _get("prom", "/api/v1/alerts", {})["data"]["alerts"]
    rows = []
    for a in alerts:
        name = a["labels"].get("alertname")
        if a["state"] != "firing" or name in IGNORED_ALERTS:
            continue
        rows.append({
            "alert": name,
            "severity": a["labels"].get("severity"),
            "namespace": a["labels"].get("namespace"),
            "target": a["labels"].get("pod") or a["labels"].get("deployment") or a["labels"].get("service"),
            "summary": a.get("annotations", {}).get("summary") or a.get("annotations", {}).get("description", "")[:200],
            "since": a.get("activeAt"),
        })
    return rows


def resource_usage(namespace: str = "default") -> list[dict]:
    """CPU / memory per pod, against limits, plus recent restarts."""
    def by_pod(q: str) -> dict[str, float]:
        res = _get("prom", "/api/v1/query", {"query": q})["data"]["result"]
        return {r["metric"].get("pod"): float(r["value"][1]) for r in res}

    ns = f'namespace="{namespace}"'
    cpu = by_pod(f"sum by (pod) (rate(container_cpu_usage_seconds_total{{{ns}}}[2m]))")
    mem = by_pod(f"sum by (pod) (container_memory_working_set_bytes{{{ns}}})")
    cpu_lim = by_pod(f'sum by (pod) (kube_pod_container_resource_limits{{{ns},resource="cpu"}})')
    mem_lim = by_pod(f'sum by (pod) (kube_pod_container_resource_limits{{{ns},resource="memory"}})')
    throttle = by_pod(f"sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{{{ns}}}[5m])) / "
                      f"sum by (pod) (rate(container_cpu_cfs_periods_total{{{ns}}}[5m]))")
    restarts = by_pod(f"sum by (pod) (increase(kube_pod_container_status_restarts_total{{{ns}}}[15m]))")
    rows = []
    for pod in sorted(set(cpu) | set(mem_lim) | set(restarts)):
        if not pod:
            continue
        row = {"pod": pod}
        if pod in cpu:
            row["cpu_cores"] = round(cpu[pod], 4)
        if pod in cpu_lim:
            row["cpu_limit"] = cpu_lim[pod]
        if pod in throttle:
            row["cpu_throttled_pct"] = round(throttle[pod] * 100, 1)
        if pod in mem:
            row["memory_mib"] = round(mem[pod] / 2**20, 1)
        if pod in mem_lim:
            row["memory_limit_mib"] = round(mem_lim[pod] / 2**20, 1)
            if pod in mem and mem_lim[pod]:
                row["memory_pct_of_limit"] = round(mem[pod] / mem_lim[pod] * 100, 1)
        row["restarts_15m"] = round(restarts.get(pod, 0))
        rows.append(row)
    return rows


# ---------- Loki ----------

def query_logs(logql: str, since_minutes: int = 30, limit: int = 50) -> dict[str, Any]:
    end = time.time_ns()
    data = _get("loki", "/loki/api/v1/query_range", {
        "query": logql, "start": end - since_minutes * 60 * 10**9, "end": end,
        "limit": limit, "direction": "backward",
    })["data"]
    if data["resultType"] != "streams":
        return {"query": logql, "result_type": data["resultType"],
                "series": [{"labels": r["metric"], "last": r["values"][-1][1] if r.get("values") else r.get("value")}
                           for r in data["result"]]}
    lines = []
    for stream in data["result"]:
        lbl = stream["stream"]
        src = lbl.get("app") or lbl.get("pod") or lbl.get("job", "?")
        for ts, line in stream["values"]:
            lines.append((int(ts), src, line[:k8s.MAX_LOG_LINE]))
    lines.sort(reverse=True)
    return {"query": logql, "since_minutes": since_minutes,
            "lines": [{"time": time.strftime("%H:%M:%S", time.gmtime(t / 1e9)) + "Z", "source": s, "line": l}
                      for t, s, l in lines[:limit]]}


def error_log_summary(namespace: str = "default", since_minutes: int = 15, samples: int = 3) -> list[dict]:
    """Which apps are logging errors right now, with a few example lines each."""
    count_q = (f'sum by (app) (count_over_time({{namespace="{namespace}"}} '
               f'|~ "{ERROR_PATTERN}" {NOISE_FILTER} [{since_minutes}m]))')
    data = _get("loki", "/loki/api/v1/query", {"query": count_q})["data"]["result"]
    counts = sorted(((r["metric"].get("app", "?"), int(float(r["value"][1]))) for r in data),
                    key=lambda x: -x[1])
    rows = []
    for app, n in counts[:8]:
        ex = query_logs(f'{{namespace="{namespace}", app="{app}"}} |~ "{ERROR_PATTERN}" {NOISE_FILTER}',
                        since_minutes=since_minutes, limit=samples)["lines"]
        rows.append({"app": app, "error_lines": n, "examples": [e["line"] for e in ex]})
    return rows
