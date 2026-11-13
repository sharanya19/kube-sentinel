---
id: pending-pods
title: Pods stuck in Pending (cannot be scheduled)
category: scheduling
components: [any]
keywords: [Pending, FailedScheduling, Insufficient cpu, Insufficient memory, node selector, taint, PersistentVolumeClaim unbound]
---

# Pods stuck in Pending (cannot be scheduled)

## Symptoms
- Pod status `Pending` for minutes, no container ever starts, no restarts, no logs.
- Event reason `FailedScheduling`, e.g. `0/1 nodes are available: 1 Insufficient memory`.

## Confirm
1. `kubectl describe pod <pod>`: read the `FailedScheduling` message.
   - `Insufficient cpu/memory` → requests are larger than free capacity.
   - `didn't match Pod's node affinity/selector` or `untolerated taint` → placement rules.
   - `pod has unbound immediate PersistentVolumeClaims` → storage problem.
2. Free capacity: `kubectl describe nodes` (Allocated resources) or `kubectl top nodes`.
3. Did requests increase in a recent deploy (`kubectl rollout history`)?

## Likely causes
- Resource requests raised beyond node capacity, or the cluster is full.
- Node selector / affinity / taints that no node satisfies.
- StorageClass missing or volume can't be provisioned.

## Fix
- Requests raised by a deploy: `kubectl rollout undo deployment/<name>`.
- Cluster full: add nodes (cluster autoscaler) or lower requests of over-provisioned workloads.
- Placement rules: correct the selector / tolerations and redeploy.

## Verify
- Pod gets scheduled (`Scheduled` event) and becomes Ready.

## Prevent
- Track requested vs allocatable capacity; alert on pods Pending longer than 5 minutes.
