---
id: scaled-to-zero
title: Deployment scaled to zero / service has no endpoints
category: scaled-to-zero
components: [any]
keywords: [replicas 0, no endpoints, connection refused, scaled down, ScalingReplicaSet]
---

# Deployment scaled to zero / service has no endpoints

## Symptoms
- Callers fail with `connection refused`, `no such host`, or gRPC `Unavailable` when talking to the service.
- The service exists, but `kubectl get endpoints <svc>` (or EndpointSlices) shows **0 ready endpoints**.
- The deployment shows `0/0` replicas. No pods are crashing: they simply don't exist, so pod-level
  dashboards look quiet.

## Confirm
1. `kubectl get deploy <name>`: desired replicas is 0.
2. `kubectl get events --field-selector involvedObject.name=<name>`: look for
   `Scaled down replica set <name>-xxxx from 1 to 0` (reason `ScalingReplicaSet`).
3. Check who changed it: `kubectl get deploy <name> -o yaml` and look at `metadata.managedFields`
   (manager and time of the last `spec.replicas` update), or the audit log.
4. Rule out an autoscaler: `kubectl get hpa` (an HPA never scales below `minReplicas`, usually 1).

## Likely causes
- A manual `kubectl scale --replicas=0` (maintenance, cost cutting, or a mistaken command).
- A script or GitOps sync applying a manifest with `replicas: 0`.
- A scale-to-zero tool (KEDA, cluster cost tools) acting on a service that must stay up.

## Fix
- Scale the deployment back to its normal replica count:
  `kubectl scale deployment/<name> --replicas=1` (use the usual production count).
- Kube-Sentinel action: `scale_deployment(name, replicas)`.

## Verify
- `kubectl rollout status deployment/<name>` completes, and the service has ready endpoints again.
- Error rate in the callers (e.g. frontend) drops back to normal within a minute.

## Prevent
- Protect critical deployments: a PodDisruptionBudget, or a policy (Kyverno / OPA) that rejects `replicas: 0`.
- Alert on `kube_deployment_spec_replicas == 0` for critical services (Kube-Sentinel: `ShopDeploymentScaledToZero`).
