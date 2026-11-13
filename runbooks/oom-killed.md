---
id: oom-killed
title: Container OOMKilled (out of memory, exit code 137)
category: oom-killed
components: [any]
keywords: [OOMKilled, exit code 137, memory limit, out of memory, CrashLoopBackOff, JVM heap]
---

# Container OOMKilled (out of memory, exit code 137)

## Symptoms
- Pod restarts repeatedly; `kubectl get pods` shows `CrashLoopBackOff` or a rising restart count.
- `lastState.terminated.reason: OOMKilled` with **exit code 137** (killed by SIGKILL).
- Memory usage climbs to the container's limit right before each restart.
- With one replica and a rolling update, the old pod may keep serving while the new one crash-loops,
  so the rollout is stuck and user impact is partial.

## Confirm
1. `kubectl describe pod <pod>`: `Last State: Terminated, Reason: OOMKilled, Exit Code: 137`.
2. Compare memory usage to the limit:
   `container_memory_working_set_bytes` vs `kube_pod_container_resource_limits{resource="memory"}`.
3. Check whether the limit changed recently: `kubectl rollout history deployment/<name>` and diff the
   last two revisions (`resources.limits.memory`).

## Likely causes
- **A recent deploy lowered the memory limit** below what the app needs (most common after a change).
- A memory leak or traffic spike pushing usage up over time.
- JVM / runtime heap sized larger than the container limit (e.g. `-Xmx` above the limit).

## Fix
- If a recent deploy lowered the limit: roll it back. `kubectl rollout undo deployment/<name>`.
  Kube-Sentinel action: `rollout_undo(name)`.
- If usage genuinely grew: raise the limit in the manifest and redeploy (needs a code/config change).
- Don't just restart: an OOM-killed container will be killed again at the same limit.

## Verify
- New pod stays `Running` with 0 restarts for several minutes.
- Memory stays comfortably below the limit (aim for under 80%).

## Prevent
- Set limits from observed peak usage plus headroom; review limit changes in code review.
- Alert on restart loops (Kube-Sentinel: `ShopPodRestarting`, `ShopPodWaiting`).
