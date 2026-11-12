# MCP server

A [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI agent the same
view of the cluster an on-call engineer has: pods, deployments, rollout history, events, logs and metrics.

Any MCP client can use it: the Kube-Sentinel agent, the MCP Inspector, or Claude Desktop.

## Tools

**Investigation (read-only, always on)**

| Tool | What it answers |
|---|---|
| `cluster_overview` | *Start here.* What's unhealthy right now: pods, deployments, services with 0 endpoints, warning events, firing alerts |
| `list_pods` / `describe_pod` | Pod status (CrashLoopBackOff, ImagePullBackOff...), restarts, last termination (OOMKilled, exit 137), resources, probes, events |
| `list_deployments` / `describe_deployment` | Replicas and plain-English rollout state: `healthy`, `scaled to zero`, `rollout ... stuck` |
| `rollout_history` | **What changed in the latest deploy**, e.g. `env.PAYMENT_SERVICE_ADDR: 50051 -> 50052`, and when it rolled out |
| `list_services` | Ready endpoints per service (0 = every call to it fails) |
| `get_events` | Kubernetes events: scaling, kills, failed pulls, failed probes |
| `get_pod_logs` | Logs straight from a pod or app (`previous=true` for a crashed container) |
| `error_log_summary` | Which apps are logging errors right now (Loki), with examples |
| `query_logs` | Any LogQL query against Loki |
| `resource_usage` | CPU / memory per pod vs limits, throttling, recent restarts |
| `query_metrics` / `query_metrics_range` | Any PromQL query, instant or over time |
| `get_firing_alerts` | Alerts currently firing in Prometheus |

**Fix actions (off by default)**

`scale_deployment`, `rollout_restart`, `rollout_undo`. These only appear when
`KUBE_SENTINEL_ALLOW_ACTIONS=true`, only work in allowed namespaces (`default`), default to `dry_run=true`,
and are meant to be called by the approval UI **after a human clicks Approve**, never by the agent on its own.

## Verified against every chaos scenario

| Scenario | What `cluster_overview` shows | The key clue |
|---|---|---|
| `redis-down` | `redis-cart` deployment *scaled to zero*, service has *no endpoints* | `error_log_summary`: `RedisConnectionException` in cartservice + frontend |
| `bad-config` | Nothing unhealthy (every pod is fine!) | `rollout_history`: `PAYMENT_SERVICE_ADDR: 'paymentservice:50051' -> 'paymentservice:50052'` |
| `oom-kill` | adservice pod `OOMKilled`, exit 137; rollout *stuck* | `rollout_history`: `limits.memory: '300Mi' -> '40Mi'` |
| `bad-image` | productcatalogservice `ImagePullBackOff`; rollout *stuck* | `rollout_history`: image tag `v0.10.7 -> v0.10.77` |
| `bad-probe` | currencyservice restarted recently (exit 137) | event `Liveness probe failed ... :7001`; `rollout_history`: probe port `7000 -> 7001` |

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**Try the tools** (connects over MCP exactly like the agent will):
```bash
python -m mcp_server.try_tools
python -m mcp_server.try_tools rollout_history "{\"name\": \"checkoutservice\"}"
python -m mcp_server.try_tools query_logs "{\"logql\": \"{app=\\\"cartservice\\\"} |= \\\"Redis\\\"\"}"
```

**Browse them visually** with the MCP Inspector (needs Node.js):
```bash
npx @modelcontextprotocol/inspector .venv/Scripts/python -m mcp_server
```

**Run as a server**: `python -m mcp_server` (stdio) or `python -m mcp_server --http` (http://127.0.0.1:8765/mcp).

## How it connects

- **Kubernetes:** your kubeconfig (same access as `kubectl`), or the in-cluster service account.
- **Prometheus & Loki:** through the Kubernetes API *service proxy*, so **no port-forward is needed**.
  Override with `PROMETHEUS_URL` / `LOKI_URL` to talk to them directly.
- **No API keys.** The server never calls an LLM.

## Design notes

- Outputs are **small, pre-digested dicts** (e.g. `"state": "scaled to zero"`, `"last_termination": "OOMKilled"`),
  not raw Kubernetes JSON, so the LLM spends its tokens on reasoning instead of parsing.
- `rollout_history` reports when a revision was **rolled out** (from scale-up events), not when its
  ReplicaSet was created. Kubernetes reuses old ReplicaSets on rollback, so creation time can be hours off.
- A pod that restarted in the last 15 minutes counts as unhealthy even if it looks `Running 1/1` right now.
  Otherwise a restart loop is invisible between restarts.
- The server's instructions warn the agent about this shop's normal background errors
  (paymentservice rejects `visa_electron` cards by design), so they aren't mistaken for the incident.
