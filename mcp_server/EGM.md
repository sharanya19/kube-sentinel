# Kube-Sentinel MCP Server (`mcp_server`)

## Overview
Model Context Protocol (MCP) server exposing pre-digested Kubernetes, Prometheus, Alertmanager, and Loki investigation tools alongside strictly gated cluster remediation actions. Read-only tools are always active for AI SRE triage and root-cause analysis, whereas the three deployment-mutating tools (`scale_deployment`, `rollout_restart`, `rollout_undo`) are registered only when explicitly enabled via environment configuration for human-approved execution.

## Interface
- Server Entry & Transport:
  - `mcp` / `INSTRUCTIONS` / `READ` / `WRITE` / `explain(fn)`: `MCPServer("kube-sentinel")` instance, SRE triage prompt instructions, MCP tool safety annotations, and Kubernetes `ApiException`-unwrapping error decorator (`[server.py:mcp]`, `[server.py:INSTRUCTIONS]`, `[server.py:READ]`, `[server.py:WRITE]`, `[server.py:explain]`).
  - `main()`: Runs MCP over stdio by default or Streamable HTTP on `127.0.0.1:8765` when `--http` (`[server.py:--http]`) is passed (`[server.py:main]`, `[__main__.py:main]`).
  - `try_tools.main()`: CLI client (`python -m mcp_server.try_tools [tool] [json_args]`) that spawns `mcp_server` over stdio, lists registered tools, and prints structured tool output (`[try_tools.py:main]`).
- Read-Only MCP Tools (`@mcp.tool(annotations=READ)` in `server.py`):
  - Triage: `cluster_overview(namespace="default")` (`[server.py:cluster_overview]`).
  - Kubernetes (`k8s.py`): `list_pods(namespace="default", only_unhealthy=False)` (`[server.py:list_pods]`, `[k8s.py:list_pods]`), `describe_pod(name, namespace="default")` (`[server.py:describe_pod]`, `[k8s.py:describe_pod]`), `list_deployments(namespace="default")` (`[server.py:list_deployments]`, `[k8s.py:list_deployments]`), `describe_deployment(name, namespace="default")` (`[server.py:describe_deployment]`, `[k8s.py:describe_deployment]`), `rollout_history(name, namespace="default")` (`[server.py:rollout_history]`, `[k8s.py:rollout_history]`), `list_services(namespace="default")` (`[server.py:list_services]`, `[k8s.py:list_services]`), `get_events(namespace="default", object_name=None, since_minutes=60, warnings_only=False)` (`[server.py:get_events]`, `[k8s.py:get_events]`), `get_pod_logs(name, namespace="default", tail_lines=50, previous=False, contains=None)` (`[server.py:get_pod_logs]`, `[k8s.py:get_pod_logs]`).
  - Logs & Metrics (`observability.py`): `error_log_summary(namespace="default", since_minutes=15)` (`[server.py:error_log_summary]`, `[observability.py:error_log_summary]`), `query_logs(logql, since_minutes=30, limit=50)` (`[server.py:query_logs]`, `[observability.py:query_logs]`), `resource_usage(namespace="default")` (`[server.py:resource_usage]`, `[observability.py:resource_usage]`), `query_metrics(promql)` (`[server.py:query_metrics]`, `[observability.py:query_metrics]`), `query_metrics_range(promql, minutes=30, step_seconds=60)` (`[server.py:query_metrics_range]`, `[observability.py:query_metrics_range]`), `get_firing_alerts()` (`[server.py:get_firing_alerts]`, `[observability.py:firing_alerts]`).
- Opt-In Remediation MCP Tools (`@mcp.tool(annotations=WRITE)`, registered only when `actions.enabled()` is true):
  - `scale_deployment(name, replicas, namespace="default", dry_run=True)` (`[server.py:scale_deployment]`, `[actions.py:scale_deployment]`).
  - `rollout_restart(name, namespace="default", dry_run=True)` (`[server.py:rollout_restart]`, `[actions.py:rollout_restart]`).
  - `rollout_undo(name, namespace="default", dry_run=True)` (`[server.py:rollout_undo]`, `[actions.py:rollout_undo]`).
- Shared Python Helpers & Configuration:
  - `core()` / `apps()` / `discovery()` / `api_configuration()` / `MAX_LOG_LINE` / `RECENT_RESTART_SECONDS`: Kubernetes API client accessors and health/log constants (`[k8s.py:core]`, `[k8s.py:apps]`, `[k8s.py:discovery]`, `[k8s.py:api_configuration]`, `[k8s.py:MAX_LOG_LINE]`, `[k8s.py:RECENT_RESTART_SECONDS]`).
  - `enabled()` / `ALLOWED_NAMESPACES` / `MAX_REPLICAS`: Action gate (`KUBE_SENTINEL_ALLOW_ACTIONS`), namespace allowlist (`KUBE_SENTINEL_ACTION_NAMESPACES`, default `"default"`), and replica cap (`5`) (`[actions.py:enabled]`, `[actions.py:ALLOWED_NAMESPACES]`, `[actions.py:MAX_REPLICAS]`).
  - `MONITORING_NS` / `PROM_SERVICE` / `LOKI_SERVICE` / `ALERTMANAGER_SERVICE` / `SERVICES` / `IGNORED_ALERTS`: Observability endpoint resolution (`MONITORING_NAMESPACE`, `PROMETHEUS_SERVICE`, `LOKI_SERVICE`, `ALERTMANAGER_SERVICE`, `PROMETHEUS_URL`, `LOKI_URL`, `ALERTMANAGER_URL`) and heartbeat alert filter (`[observability.py:MONITORING_NS]`, `[observability.py:PROM_SERVICE]`, `[observability.py:LOKI_SERVICE]`, `[observability.py:ALERTMANAGER_SERVICE]`, `[observability.py:SERVICES]`, `[observability.py:IGNORED_ALERTS]`).

## Invariants
- Cluster-mutating tools are registered on `mcp` if and only if `actions.enabled()` returns `True` (`KUBE_SENTINEL_ALLOW_ACTIONS` in `{"1", "true", "yes"}`) (`[actions.py:enabled]`, `[server.py:scale_deployment]`).
- Every action in `actions.py` enforces `namespace in ALLOWED_NAMESPACES` via `_check(namespace)` (`[actions.py:_check]`, `[actions.py:ALLOWED_NAMESPACES]`), defaults to `dry_run=True`, caps `scale_deployment` replicas to `0 <= replicas <= MAX_REPLICAS` (`5`) (`[actions.py:scale_deployment]`, `[actions.py:MAX_REPLICAS]`), and requires at least two owned ReplicaSets (`len(rs_list) >= 2`) with `pod-template-hash` stripped before `rollout_undo` (`[actions.py:rollout_undo]`).
- Streamable HTTP mode (`--http`) binds strictly to `127.0.0.1:8765` (`[server.py:main]`).
- `_pod_summary` treats any pod whose container terminated within `RECENT_RESTART_SECONDS` (`900`s) as `healthy = False` even if currently `Running` and ready, exposing active restart loops (`[k8s.py:_pod_summary]`, `[k8s.py:RECENT_RESTART_SECONDS]`).
- Log lines are capped at `MAX_LOG_LINE` (`300` chars) (`[k8s.py:MAX_LOG_LINE]`, `[k8s.py:get_pod_logs]`, `[observability.py:query_logs]`), `firing_alerts()` suppresses `IGNORED_ALERTS` (`Watchdog`, `InfoInhibitor`) (`[observability.py:firing_alerts]`, `[observability.py:IGNORED_ALERTS]`), and `cluster_overview` isolates Prometheus/Alertmanager failures into `firing_alerts_error` without failing Kubernetes triage (`[server.py:cluster_overview]`).

## Side Effects
- Reads local kubeconfig (`config.load_kube_config()`) or in-cluster service account credentials (`config.load_incluster_config()`) and queries Kubernetes `CoreV1Api`, `AppsV1Api`, and `DiscoveryV1Api` endpoints (`[k8s.py:_load]`, `[k8s.py:core]`, `[k8s.py:apps]`, `[k8s.py:discovery]`).
- Sends HTTP `GET` requests via `httpx.Client` to Prometheus, Loki, and Alertmanager directly (when `PROMETHEUS_URL`, `LOKI_URL`, or `ALERTMANAGER_URL` is set) or through the Kubernetes API service proxy (`/api/v1/namespaces/{MONITORING_NS}/services/{service}/proxy`) (`[observability.py:_client]`, `[observability.py:_get]`).
- Patches live Kubernetes Deployments via `patch_namespaced_deployment_scale` or `patch_namespaced_deployment` when `KUBE_SENTINEL_ALLOW_ACTIONS` is enabled and `dry_run=False` (`[actions.py:scale_deployment]`, `[actions.py:rollout_restart]`, `[actions.py:rollout_undo]`).
- Serves MCP traffic over `stdio` or binds `127.0.0.1:8765` (`--http`) (`[server.py:main]`), and `try_tools.py` spawns a `python -m mcp_server` child process over `stdio` (`[try_tools.py:main]`).

## Verification
- Run: `<test-command>`
