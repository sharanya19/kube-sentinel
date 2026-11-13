---
id: failing-probes
title: Liveness or readiness probe failing (misconfigured health check)
category: bad-probe
components: [any]
keywords: [Liveness probe failed, Readiness probe failed, Unhealthy, health check, probe port, context deadline exceeded, restarts]
---

# Liveness or readiness probe failing (misconfigured health check)

## Symptoms
- Events with reason `Unhealthy`: `Liveness probe failed: ...` or `Readiness probe failed: ...`,
  e.g. `failed to connect service ":7001" within 1s: context deadline exceeded`.
- **Liveness** failures: the kubelet kills and restarts the container (exit code 137), so restarts climb
  even though the app itself is fine. Between restarts the pod looks `Running 1/1`.
- **Readiness** failures: the pod stays `0/1` and is removed from service endpoints; no restarts.
- Callers see intermittent errors (connection refused while the container restarts).

## Confirm
1. `kubectl describe pod <pod>`: read the probe definition (port, path, gRPC service) and the `Unhealthy` events.
2. Compare the probe port with the port the app actually listens on (container `ports`, `PORT` env var).
3. `kubectl rollout history deployment/<name>` and diff: did the probe change in the latest revision?
4. Test the endpoint from inside the pod (`grpc_health_probe -addr=:7000` or `curl localhost:8080/healthz`).

## Likely causes
- **Probe points at the wrong port or path** after a deploy (most common when only the probe changed).
- Timeouts too short (`timeoutSeconds: 1`) for a slow endpoint, or `initialDelaySeconds` too short for a slow start.
- The health endpoint depends on a downstream service, so a dependency outage restarts healthy pods.

## Fix
- Probe changed in a recent deploy: `kubectl rollout undo deployment/<name>`.
  Kube-Sentinel action: `rollout_undo(name)`.
- Slow starts: add a `startupProbe` or raise `initialDelaySeconds` / `timeoutSeconds`.
- Don't make liveness depend on downstream services; check only the process itself.

## Verify
- No new `Unhealthy` events; restart count stops increasing for 5+ minutes.

## Prevent
- Keep probe ports defined by name (`port: grpc`) so they follow the container port.
- Alert on restart loops (Kube-Sentinel: `ShopPodRestarting`).
