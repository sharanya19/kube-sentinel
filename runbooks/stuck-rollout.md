---
id: stuck-rollout
title: Rollout stuck - new pods never become ready
category: rollout-stuck
components: [any]
keywords: [rollout stuck, ProgressDeadlineExceeded, rolling update, old replica set, maxUnavailable, new pods not ready]
---

# Rollout stuck: new pods never become ready

## Symptoms
- `kubectl rollout status deployment/<name>` hangs; eventually condition `Progressing=False`,
  reason `ProgressDeadlineExceeded` (after 10 minutes by default).
- Two ReplicaSets exist: the old one still serves traffic, the new one has pods that never become ready.
- With `maxUnavailable: 25%` and a single replica, users may see **little or no impact**, which is why
  stuck rollouts go unnoticed until the next incident.

## Confirm
1. `kubectl get rs -l app=<name>`: compare READY counts of the newest and previous ReplicaSets.
2. Find **why** the new pods aren't ready, then follow the matching runbook:
   - `ImagePullBackOff` / `ErrImagePull` → image pull error runbook
   - `OOMKilled`, exit 137 → OOMKilled runbook
   - `Unhealthy` probe events → failing probes runbook
   - other non-zero exits → CrashLoopBackOff runbook
   - `Pending` with `FailedScheduling` → pending pods runbook
3. `kubectl rollout history deployment/<name>`: what changed in the newest revision.

## Fix
- Return to the last working revision: `kubectl rollout undo deployment/<name>`.
  Kube-Sentinel action: `rollout_undo(name)`. This removes the broken pods and keeps the old ones.
- Then fix the change itself before redeploying.

## Verify
- Only one ReplicaSet has pods; `rollout status` reports success.

## Prevent
- Alert on stuck rollouts (Kube-Sentinel: `ShopRolloutStuck`), set a sensible `progressDeadlineSeconds`,
  and use progressive delivery (canary) with automatic rollback.
