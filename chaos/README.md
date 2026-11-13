# Failure scenarios (chaos)

Scripts that break Online Boutique **on purpose**, in ways that happen in real production systems.
Each scenario has a known correct answer (its *ground truth*), used to check the agent's diagnosis
([results](../agent/#results)).

## Scenarios

| ID | What breaks | What a shopper sees | Where the clue is |
|---|---|---|---|
| `redis-down` | `redis-cart` scaled to 0 replicas | **Whole site HTTP 500** | cartservice logs: `RedisConnectionException`; deployment 0/0 |
| `bad-config` | checkoutservice `PAYMENT_SERVICE_ADDR` set to the wrong port (50052) | Browsing works, **every order fails** | frontend logs: `failed to charge card ... :50052: i/o timeout` |
| `oom-kill` | adservice memory limit lowered to 40Mi | Nothing yet: **rollout stuck** | new pod `CrashLoopBackOff`, last state `OOMKilled` (exit 137) |
| `bad-image` | productcatalogservice image tag typo `v0.10.77` | Nothing yet: **rollout stuck** | new pod `ImagePullBackOff`; event `Failed to pull image` |
| `bad-probe` | currencyservice liveness probe on port 7001 (app uses 7000) | **Random page failures** | restart count climbing; event `Liveness probe failed ... :7001` |

All five were tested on minikube: each one breaks the shop as described, and `restore` brings it back to healthy.

### Why some failures are "silent"
Each shop service runs **one** replica. When a bad version rolls out, Kubernetes keeps the old pod
running until the new one is ready, and the new one never is. So the site stays up, but the deployment
is stuck, one pod death away from an outage. Good SREs (and a good agent) catch these *before* users do.

## Usage

```bash
python chaos/chaos.py list                 # show scenarios
python chaos/chaos.py break redis-down     # inject a failure
python chaos/chaos.py status               # active scenarios + pod health
python chaos/chaos.py restore redis-down   # undo it
python chaos/chaos.py restore --all        # undo everything
```

No extra Python packages needed, only `kubectl` pointed at your cluster.

## How it works

- [`scenarios.py`](scenarios.py) defines each scenario: what to change, the expected user impact, and its
  **ground truth** (root cause, broken component, category, fix command, evidence, scoring keywords).
- [`chaos.py`](chaos.py) applies the change with `kubectl scale` or a JSON patch. **Before** changing anything
  it saves a snapshot of the deployment to `chaos/.state/` (git-ignored), and `restore` puts back exactly
  that snapshot.
- Only one scenario can be active at a time (unless you pass `--allow-multiple`), so every test has a
  single, clear cause.

## Adding a scenario

Add a `Scenario(...)` entry to `SCENARIOS` in `scenarios.py` with either `scale_to=` or a `patch=` function
that returns JSON Patch operations, plus its `GroundTruth`. `break` and `restore` pick it up automatically.
