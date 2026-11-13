---
id: wrong-service-address
title: Misconfiguration - service calls the wrong address or port
category: misconfiguration
components: [any]
keywords: [env var, wrong port, i/o timeout, connection refused, dial tcp, misconfiguration, SERVICE_ADDR, config change]
---

# Misconfiguration: service calls the wrong address or port

## Symptoms
- **Every pod is healthy** (Running, no restarts, endpoints ready), yet a user flow fails, e.g. checkout.
- Callers log errors like `dial tcp 10.x.x.x:50052: i/o timeout`, `connection refused`, or
  `failed to charge card: rpc error: code = Unavailable`.
- The failing port or hostname in the error doesn't match what the target service listens on.
- Started right after a deploy of the **calling** service, not the called one.

## Confirm
1. Find the failing call in logs (often in the caller's caller, e.g. frontend logs checkout errors).
2. Note the address in the error and compare it with the target service's ports:
   `kubectl get svc <target>`.
3. `kubectl rollout history deployment/<caller>` and diff the last two revisions: look for env vars
   such as `*_SERVICE_ADDR`, `*_HOST`, `*_PORT`, or a changed ConfigMap.
4. Confirm the target itself is healthy (ready endpoints, no errors in its own logs).

## Likely causes
- **An env var or ConfigMap value changed in the last deploy** (typo in a port or host).
- A service was renamed or its port changed without updating its callers.
- DNS / network policy changes blocking the path.

## Fix
- Bad value from the latest deploy of the caller: `kubectl rollout undo deployment/<caller>`.
  Kube-Sentinel action: `rollout_undo(name)` on the **caller**, not the healthy target.
- If the target genuinely moved: update the caller's config to the new address and redeploy.

## Verify
- The user flow (e.g. checkout) succeeds; caller error logs stop within a minute.

## Prevent
- Validate service addresses in CI (contract tests, config schema checks).
- Alert on error logs, not only on pod health: this failure crashes nothing
  (Kube-Sentinel: `ShopErrorLogs` from Loki).
