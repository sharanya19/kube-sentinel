---
id: cart-redis
title: Online Boutique - cart unavailable (redis-cart / cartservice)
category: dependency-failure
components: [redis-cart, cartservice, frontend]
keywords: [Redis, RedisConnectionException, cart storage, can't access cart storage, StackExchange.Redis, HTTP 500]
---

# Online Boutique: cart unavailable (redis-cart / cartservice)

Service-specific runbook for the shop's cart path: `frontend → cartservice → redis-cart`.

## Symptoms
- **The whole site returns HTTP 500**, not just the cart page: every page renders the cart item count,
  so every page calls cartservice.
- frontend logs: `could not retrieve cart: rpc error: code = FailedPrecondition desc = Can't access cart storage`.
- cartservice logs: `StackExchange.Redis.RedisConnectionException: It was not possible to connect to the
  redis server(s)`, `ConnectTimeout`.

## Confirm
1. `kubectl get deploy redis-cart`: is it `0/0` (scaled to zero) or `0/1` (not ready)?
2. `kubectl get endpointslices -l kubernetes.io/service-name=redis-cart`: 0 ready endpoints means nothing serves Redis.
3. If redis-cart is running, check `kubectl logs deploy/redis-cart` and its memory (Redis can be OOMKilled).

## Likely causes
- redis-cart **scaled to 0** (manual scale, script or cost tooling).
- redis-cart pod crash-looping or OOMKilled.
- `REDIS_ADDR` env var in cartservice changed to a wrong host/port (misconfiguration).

## Fix
- Scaled to 0: `kubectl scale deployment/redis-cart --replicas=1`. Kube-Sentinel action: `scale_deployment`.
- Crash or OOM: follow the CrashLoopBackOff or OOMKilled runbook for redis-cart.
- Wrong `REDIS_ADDR`: `kubectl rollout undo deployment/cartservice`.

## Verify
- Home page returns 200; cartservice logs stop showing Redis errors.
- Note: this demo Redis has no persistent volume, so carts are empty after a restart (expected).

## Prevent
- Never let redis-cart drop to 0 replicas (alert `ShopDeploymentScaledToZero`); in production use a
  managed Redis or a StatefulSet with persistence.
