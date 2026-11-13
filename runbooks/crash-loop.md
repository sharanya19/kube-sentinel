---
id: crash-loop
title: CrashLoopBackOff - application exits on startup
category: crash-loop
components: [any]
keywords: [CrashLoopBackOff, exit code 1, panic, missing environment variable, secret, configmap, stack trace]
---

# CrashLoopBackOff: application exits on startup

Use this when the container exits **by itself** (exit code 1, 2, 255 ...). If the reason is
`OOMKilled` / exit 137, use the OOMKilled runbook; if events show probe failures, use the probe runbook.

## Symptoms
- Pod status `CrashLoopBackOff`, restart count rising, back-off delay growing up to 5 minutes.
- `lastState.terminated.exitCode` is non-zero and not 137.

## Confirm
1. Read the crashed container's logs: `kubectl logs <pod> --previous`.
   Look for a stack trace, `panic:`, `missing required env`, `permission denied`, `address already in use`.
2. `kubectl describe pod <pod>`: exit code and events (e.g. `CreateContainerConfigError` = missing Secret/ConfigMap).
3. `kubectl rollout history deployment/<name>`: did the image, command, args or env change?

## Likely causes
- Bug in a new image version (crashes on startup).
- Missing or wrong config: env var, Secret or ConfigMap key renamed or deleted.
- Dependency required at startup is unavailable and the app exits instead of retrying.

## Fix
- New version crashes: `kubectl rollout undo deployment/<name>`. Kube-Sentinel action: `rollout_undo(name)`.
- Missing Secret/ConfigMap: recreate it, then `kubectl rollout restart deployment/<name>`.
- Startup dependency: fix the dependency; consider retries with back-off in the app.

## Verify
- Pod `Running` with stable restart count; logs show a normal startup.

## Prevent
- Smoke-test images in staging; use readiness gates so a broken version never takes traffic.
