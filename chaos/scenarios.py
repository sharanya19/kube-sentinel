"""Failure scenarios for Online Boutique.

Each scenario breaks the shop in one known way. The `ground_truth` block is the
correct answer the agent should find; the evaluation step scores against it.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GroundTruth:
    root_cause: str          # one-sentence correct diagnosis
    component: str           # the deployment that is actually broken
    category: str            # short label, e.g. "scaled-to-zero"
    fix: str                 # the command a human would run to fix it
    evidence: list[str]      # signals the agent should be able to find
    keywords: list[str]      # words a correct diagnosis should mention (used for scoring)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    deployment: str
    description: str         # what the script does to the cluster
    user_impact: str         # what a shopper would notice
    # How to break it: either "scale" with a replica count, or a JSON patch
    # built by `patch(deployment_json)`.
    scale_to: int | None = None
    patch: object = None     # callable(dict) -> list[dict] (JSON Patch ops)
    ground_truth: GroundTruth = field(default=None)


def _env_index(dep: dict, name: str) -> int:
    env = dep["spec"]["template"]["spec"]["containers"][0].get("env", [])
    for i, e in enumerate(env):
        if e["name"] == name:
            return i
    raise KeyError(f"env var {name} not found")


C0 = "/spec/template/spec/containers/0"

SCENARIOS: list[Scenario] = [
    Scenario(
        id="redis-down",
        title="Cart database scaled to zero",
        deployment="redis-cart",
        description="Scales the redis-cart deployment to 0 replicas.",
        user_impact="The whole site returns HTTP 500: every page shows the cart count, so every page needs Redis.",
        scale_to=0,
        ground_truth=GroundTruth(
            root_cause="The redis-cart deployment was scaled to 0 replicas, so cartservice cannot reach Redis.",
            component="redis-cart",
            category="scaled-to-zero",
            fix="kubectl scale deployment/redis-cart --replicas=1",
            evidence=[
                "deployment redis-cart has 0/0 replicas",
                "event: Scaled down replica set redis-cart-... to 0",
                "cartservice logs: RedisConnectionException ... It was not possible to connect to the redis server(s)",
                "frontend returns HTTP 500 on every page",
            ],
            keywords=["redis-cart", "replica"],
        ),
    ),
    Scenario(
        id="bad-config",
        title="Checkout pointed at the wrong payment port",
        deployment="checkoutservice",
        description="Changes checkoutservice env PAYMENT_SERVICE_ADDR to paymentservice:50052 (real port is 50051).",
        user_impact="Browsing works, but every order fails at 'Place order'.",
        patch=lambda dep: [{
            "op": "replace",
            "path": f"{C0}/env/{_env_index(dep, 'PAYMENT_SERVICE_ADDR')}/value",
            "value": "paymentservice:50052",
        }],
        ground_truth=GroundTruth(
            root_cause="A new checkoutservice rollout set PAYMENT_SERVICE_ADDR to the wrong port (50052 instead of 50051), so payment calls fail.",
            component="checkoutservice",
            category="misconfiguration",
            fix="kubectl rollout undo deployment/checkoutservice",
            evidence=[
                "checkoutservice was rolled out recently (new ReplicaSet)",
                "frontend logs: failed to charge card ... dial tcp ...:50052: i/o timeout",
                "paymentservice itself is healthy and receives no requests",
                "frontend errors on /cart/checkout",
            ],
            keywords=["checkoutservice", "PAYMENT_SERVICE_ADDR", "50052"],
        ),
    ),
    Scenario(
        id="oom-kill",
        title="Ad service memory limit too low",
        deployment="adservice",
        description="Rolls out adservice with a 40Mi memory limit (normal: 300Mi). The Java process is OOMKilled on start.",
        user_impact="Little at first: the old pod keeps serving because the new one never becomes ready. The rollout is stuck.",
        patch=lambda dep: [
            {"op": "replace", "path": f"{C0}/resources/limits/memory", "value": "40Mi"},
            {"op": "replace", "path": f"{C0}/resources/requests/memory", "value": "40Mi"},
        ],
        ground_truth=GroundTruth(
            root_cause="A new adservice rollout lowered the memory limit to 40Mi; the new pods are OOMKilled and stuck in CrashLoopBackOff.",
            component="adservice",
            category="oom-killed",
            fix="kubectl rollout undo deployment/adservice",
            evidence=[
                "new adservice pod status: CrashLoopBackOff, last state OOMKilled (exit code 137)",
                "container memory limit 40Mi",
                "rollout not progressing (old ReplicaSet still serving)",
            ],
            keywords=["adservice", "OOM", "memory"],
        ),
    ),
    Scenario(
        id="bad-image",
        title="Product catalog deployed with a non-existent image tag",
        deployment="productcatalogservice",
        description="Rolls out productcatalogservice with image tag v0.10.77 (a typo; it does not exist).",
        user_impact="Little at first: the old pod keeps serving. The rollout is stuck, and the service would go down if the old pod died.",
        patch=lambda dep: [{
            "op": "replace",
            "path": f"{C0}/image",
            "value": dep["spec"]["template"]["spec"]["containers"][0]["image"].rsplit(":", 1)[0] + ":v0.10.77",
        }],
        ground_truth=GroundTruth(
            root_cause="A new productcatalogservice rollout uses image tag v0.10.77, which does not exist; the new pod is stuck in ImagePullBackOff.",
            component="productcatalogservice",
            category="image-pull-error",
            fix="kubectl rollout undo deployment/productcatalogservice",
            evidence=[
                "new productcatalogservice pod status: ImagePullBackOff / ErrImagePull",
                "event: Failed to pull image ...:v0.10.77 ... not found",
                "rollout not progressing (old ReplicaSet still serving)",
            ],
            keywords=["productcatalogservice", "image", "v0.10.77"],
        ),
    ),
    Scenario(
        id="bad-probe",
        title="Currency service liveness probe on the wrong port",
        deployment="currencyservice",
        description="Rolls out currencyservice with its liveness probe on port 7001 (the app listens on 7000). Kubernetes keeps killing a healthy container.",
        user_impact="Pages fail intermittently (about 1 in 10 in testing): currencyservice is restarted every ~30s. Every page shows prices, so the whole site is affected.",
        patch=lambda dep: [
            {"op": "replace", "path": f"{C0}/livenessProbe/grpc/port", "value": 7001},
        ],
        ground_truth=GroundTruth(
            root_cause="A new currencyservice rollout points the liveness probe at port 7001 while the app listens on 7000, so Kubernetes restarts the container repeatedly.",
            component="currencyservice",
            category="bad-probe",
            fix="kubectl rollout undo deployment/currencyservice",
            evidence=[
                "currencyservice restart count rising (3 restarts in ~100s)",
                "event: Liveness probe failed: failed to connect service \"...:7001\"",
                "app logs show normal startup (the app itself is healthy)",
                "frontend errors calling currencyservice",
            ],
            keywords=["currencyservice", "liveness", "probe"],
        ),
    ),
]

BY_ID = {s.id: s for s in SCENARIOS}
