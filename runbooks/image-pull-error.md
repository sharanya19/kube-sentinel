---
id: image-pull-error
title: ImagePullBackOff / ErrImagePull
category: image-pull-error
components: [any]
keywords: [ImagePullBackOff, ErrImagePull, manifest unknown, image tag, registry, not found, unauthorized]
---

# ImagePullBackOff / ErrImagePull

## Symptoms
- New pod stuck in `ErrImagePull`, then `ImagePullBackOff`; it never starts.
- Rollout is stuck: the new ReplicaSet has 0 ready pods while the old one keeps serving.
- Events: `Failed to pull image "...": manifest unknown`, `not found`, or `unauthorized`.

## Confirm
1. `kubectl describe pod <pod>`: read the `Failed` event message.
   - `manifest unknown` / `not found` → the tag or image name doesn't exist.
   - `unauthorized` / `denied` → registry credentials (imagePullSecrets) are missing or wrong.
   - `i/o timeout` / `no such host` → the node can't reach the registry.
2. `kubectl rollout history deployment/<name>` and diff the last two revisions: did `image` change?
3. Check the tag exists in the registry (e.g. `crane ls <repo>` or the registry UI).

## Likely causes
- **Typo or non-existent tag in a deploy** (e.g. `v0.10.77` instead of `v0.10.7`), or a tag that was
  never pushed because the build failed.
- Expired or missing pull secret for a private registry.
- Registry outage or network policy blocking egress.

## Fix
- Bad tag from a recent deploy: `kubectl rollout undo deployment/<name>` to return to the last good image.
  Kube-Sentinel action: `rollout_undo(name)`.
- Credentials: recreate the `imagePullSecret` and restart the rollout.
- Registry outage: wait or point to a mirror; old pods keep serving meanwhile.

## Verify
- `kubectl rollout status deployment/<name>` completes; the pod shows the expected image tag and is `Running`.

## Prevent
- Deploy by immutable digest or only tags the CI pipeline has confirmed were pushed.
- Alert on pods waiting in `ImagePullBackOff` (Kube-Sentinel: `ShopPodWaiting`, `ShopRolloutStuck`).
