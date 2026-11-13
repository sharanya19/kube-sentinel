---
id: cpu-throttling
title: High latency from CPU throttling or saturation
category: resource-exhaustion
components: [any]
keywords: [latency, slow, p99, CPU throttling, cfs_throttled, CPU limit, saturation, timeouts]
---

# High latency from CPU throttling or saturation

## Symptoms
- p95/p99 latency rises; callers see timeouts (`DeadlineExceeded`) but **no crashes or restarts**.
- `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total` above ~25%.
- CPU usage flat at the container's CPU limit.

## Confirm
1. Throttling ratio per pod (PromQL):
   `sum by (pod) (rate(container_cpu_cfs_throttled_periods_total[5m])) / sum by (pod) (rate(container_cpu_cfs_periods_total[5m]))`.
2. CPU usage vs limit: `rate(container_cpu_usage_seconds_total[5m])` vs `kube_pod_container_resource_limits{resource="cpu"}`.
3. Did traffic increase (request rate) or did the CPU limit decrease in a recent deploy?

## Likely causes
- Traffic growth beyond what the replicas can handle.
- CPU limit lowered in a recent deploy, or an expensive code path introduced.
- Noisy neighbour on an overloaded node (check node CPU).

## Fix
- Limit lowered in a recent deploy: `kubectl rollout undo deployment/<name>`.
- Traffic growth: scale out, `kubectl scale deployment/<name> --replicas=N` (Kube-Sentinel: `scale_deployment`),
  then add an HPA.
- Consider removing CPU limits for latency-sensitive services (keep requests).

## Verify
- Throttling ratio below 10%, latency back to baseline.

## Prevent
- HPA on CPU or request rate; alert on throttling and latency SLOs, not only on errors.
