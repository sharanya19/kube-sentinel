# Chaos

## Overview
Provides deterministic fault-injection and state-restoration tooling for the Online Boutique microservices in the `default` Kubernetes namespace, paired with structured ground-truth metadata for evaluating Kube-Sentinel's root-cause diagnoses (see [README.md](./README.md)). Snapshots target deployments prior to mutation so every injected incident can be cleanly rolled back without polluting rollout history.

## Interface
- `GroundTruth` (`@dataclass(frozen=True)`) — Immutable evaluation schema holding `root_cause: str`, `component: str`, `category: str`, `fix: str`, `evidence: list[str]`, and `keywords: list[str]` `[scenarios.py:GroundTruth]`.
- `Scenario` (`@dataclass(frozen=True)`) — Immutable fault specification defining `id: str`, `title: str`, `deployment: str`, `description: str`, `user_impact: str`, `scale_to: int | None`, `patch: object` (callable returning RFC 6902 JSON Patch ops), and `ground_truth: GroundTruth` `[scenarios.py:Scenario]`.
- `SCENARIOS: list[Scenario]` and `BY_ID: dict[str, Scenario]` — Registry of five built-in scenarios targeting container path `C0 = "/spec/template/spec/containers/0"`: `redis-down` (`redis-cart` scaled to `0`), `bad-config` (`checkoutservice` `PAYMENT_SERVICE_ADDR` set to `paymentservice:50052`), `oom-kill` (`adservice` memory request/limit lowered to `40Mi`), `bad-image` (`productcatalogservice` image tag set to `:v0.10.77`), and `bad-probe` (`currencyservice` gRPC liveness probe port set to `7001`) `[scenarios.py:SCENARIOS]`, `[scenarios.py:BY_ID]`, `[scenarios.py:C0]`.
- [chaos.py](./chaos.py) CLI (`main()`) — `argparse` command-line interface supporting `list` (`cmd_list`), `break <scenario> [--allow-multiple]` (`cmd_break`), `restore [<scenario>] [--all] [--no-wait]` (`cmd_restore`), and `status` (`cmd_status`) `[chaos.py:main]`, `[chaos.py:cmd_list]`, `[chaos.py:cmd_break]`, `[chaos.py:--allow-multiple]`, `[chaos.py:cmd_restore]`, `[chaos.py:--all]`, `[chaos.py:--no-wait]`, `[chaos.py:cmd_status]`.
- Helper functions and constants in [chaos.py](./chaos.py) — `NAMESPACE = "default"`, `STATE_DIR = Path(__file__).resolve().parent / ".state"`, `kubectl(*args, input_text=None) -> str`, `get_deployment(name) -> dict`, `state_file(scenario_id) -> Path`, `restore_one(scenario_id, wait) -> None`, and `delete_broken_replicasets(deployment) -> None` `[chaos.py:NAMESPACE]`, `[chaos.py:STATE_DIR]`, `[chaos.py:kubectl]`, `[chaos.py:get_deployment]`, `[chaos.py:state_file]`, `[chaos.py:restore_one]`, `[chaos.py:delete_broken_replicasets]`.
- Subdirectories: None (runtime snapshot directory `.state/` is non-code local state).

## Invariants
- `GroundTruth` and `Scenario` dataclasses are frozen (`frozen=True`); each `Scenario` mutates a deployment via either `scale_to` or a JSON Patch builder (`patch(dep)`) targeting container index `0` (`C0`), and `_env_index` raises `KeyError` if a targeted environment variable is absent `[scenarios.py:GroundTruth]`, `[scenarios.py:Scenario]`, `[scenarios.py:C0]`, `[scenarios.py:_env_index]`.
- Every `kubectl()` call automatically prepends `-n default` (`NAMESPACE = "default"`) and raises `RuntimeError` if `subprocess.run` exits non-zero `[chaos.py:NAMESPACE]`, `[chaos.py:kubectl]`.
- `cmd_break` enforces single-scenario isolation by inspecting `STATE_DIR` (`chaos/.state/*.json`): it aborts via `sys.exit` if `<scenario>.json` already exists or if any other scenario is active unless `--allow-multiple` is passed `[chaos.py:cmd_break]`, `[chaos.py:STATE_DIR]`, `[chaos.py:--allow-multiple]`.
- `cmd_break` reads the live deployment (`get_deployment`) to snapshot `spec.replicas` and `spec.template.spec.containers` before applying `kubectl scale` or `kubectl patch --type=json`, persisting `chaos/.state/<scenario>.json` once the mutation succeeds `[chaos.py:cmd_break]`, `[chaos.py:get_deployment]`, `[chaos.py:state_file]`.
- During restoration (`restore_one`), unless `--no-wait` (`wait=False`) is passed, the script waits up to `300s` for `kubectl rollout status` and invokes `delete_broken_replicasets` to delete `0`-replica `ReplicaSet`s owned by the deployment before unlinking the snapshot file, keeping rollout history clean across repeated runs `[chaos.py:restore_one]`, `[chaos.py:delete_broken_replicasets]`, `[chaos.py:--no-wait]`.

## Side Effects
- Spawns `kubectl` subprocesses (`get`, `scale`, `patch`, `rollout status`, `delete replicaset`) via `subprocess.run` that mutate live `Deployment` specs and delete inactive `ReplicaSet`s in Kubernetes namespace `default` `[chaos.py:kubectl]`, `[chaos.py:cmd_break]`, `[chaos.py:restore_one]`, `[chaos.py:delete_broken_replicasets]`, `[chaos.py:cmd_status]`.
- Creates `chaos/.state/` (`STATE_DIR`), writes JSON snapshot files (`chaos/.state/<scenario_id>.json`) on `break`, and deletes them (`path.unlink()`) on `restore` `[chaos.py:STATE_DIR]`, `[chaos.py:state_file]`, `[chaos.py:cmd_break]`, `[chaos.py:restore_one]`.

## Verification
- Run: `<test-command>`
