"""Break Online Boutique on purpose, and put it back.

Usage:
    python chaos/chaos.py list
    python chaos/chaos.py break <scenario-id>
    python chaos/chaos.py restore <scenario-id>
    python chaos/chaos.py restore --all
    python chaos/chaos.py status

Before breaking anything, the script saves a snapshot of the deployment in
chaos/.state/. `restore` puts the replicas and containers back exactly as they were.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios import BY_ID, SCENARIOS  # noqa: E402

STATE_DIR = Path(__file__).resolve().parent / ".state"
NAMESPACE = "default"


def kubectl(*args: str, input_text: str | None = None) -> str:
    cmd = ["kubectl", "-n", NAMESPACE, *args]
    result = subprocess.run(cmd, capture_output=True, text=True, input=input_text)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout


def get_deployment(name: str) -> dict:
    return json.loads(kubectl("get", "deployment", name, "-o", "json"))


def state_file(scenario_id: str) -> Path:
    return STATE_DIR / f"{scenario_id}.json"


def cmd_list(_args) -> None:
    for s in SCENARIOS:
        print(f"{s.id:<12} {s.title}")
        print(f"{'':<12} target: {s.deployment}  |  impact: {s.user_impact}\n")


def cmd_break(args) -> None:
    s = BY_ID.get(args.scenario)
    if not s:
        sys.exit(f"Unknown scenario '{args.scenario}'. Run: python chaos/chaos.py list")
    if state_file(s.id).exists():
        sys.exit(f"'{s.id}' is already active. Restore it first: python chaos/chaos.py restore {s.id}")
    active = [p.stem for p in STATE_DIR.glob("*.json")] if STATE_DIR.exists() else []
    if active and not args.allow_multiple:
        sys.exit(f"Another scenario is active: {active}. Restore it first, or pass --allow-multiple.")

    dep = get_deployment(s.deployment)
    spec = dep["spec"]
    snapshot = {
        "scenario": s.id,
        "deployment": s.deployment,
        "broken_at": datetime.now(timezone.utc).isoformat(),
        "replicas": spec["replicas"],
        "containers": spec["template"]["spec"]["containers"],
    }

    if s.scale_to is not None:
        kubectl("scale", "deployment", s.deployment, f"--replicas={s.scale_to}")
    else:
        ops = s.patch(dep)
        kubectl("patch", "deployment", s.deployment, "--type=json", "-p", json.dumps(ops))

    STATE_DIR.mkdir(exist_ok=True)
    state_file(s.id).write_text(json.dumps(snapshot, indent=2))
    print(f"BROKEN: {s.id} - {s.description}")
    print(f"Expected impact: {s.user_impact}")
    print(f"Undo with: python chaos/chaos.py restore {s.id}")


def delete_broken_replicasets(deployment: str) -> None:
    """Remove the deployment's inactive ReplicaSets (the broken revision) after a restore.

    Otherwise breaking the same scenario again would re-use the old broken ReplicaSet, which
    looks like a rollback to an earlier config and muddies the rollout history the agent reads.
    """
    data = json.loads(kubectl("get", "replicasets", "-o", "json"))
    for rs in data["items"]:
        owners = rs["metadata"].get("ownerReferences", [])
        if any(o["kind"] == "Deployment" and o["name"] == deployment for o in owners) \
                and rs["spec"].get("replicas", 0) == 0:
            kubectl("delete", "replicaset", rs["metadata"]["name"], "--wait=false")


def restore_one(scenario_id: str, wait: bool) -> None:
    path = state_file(scenario_id)
    if not path.exists():
        print(f"'{scenario_id}' is not active; nothing to restore.")
        return
    snap = json.loads(path.read_text())
    # A JSON merge patch replaces the whole containers list, so this restores it exactly.
    patch = {"spec": {"replicas": snap["replicas"],
                      "template": {"spec": {"containers": snap["containers"]}}}}
    kubectl("patch", "deployment", snap["deployment"], "--type=merge", "-p", json.dumps(patch))
    if wait:
        print(f"Restoring {snap['deployment']}, waiting for rollout...")
        kubectl("rollout", "status", "deployment", snap["deployment"], "--timeout=300s")
        delete_broken_replicasets(snap["deployment"])
    path.unlink()
    print(f"RESTORED: {scenario_id}")


def cmd_restore(args) -> None:
    if args.all:
        ids = [p.stem for p in STATE_DIR.glob("*.json")] if STATE_DIR.exists() else []
        if not ids:
            print("No active scenarios.")
        for sid in ids:
            restore_one(sid, wait=not args.no_wait)
    elif args.scenario:
        restore_one(args.scenario, wait=not args.no_wait)
    else:
        sys.exit("Give a scenario id or --all")


def cmd_status(_args) -> None:
    ids = [p.stem for p in STATE_DIR.glob("*.json")] if STATE_DIR.exists() else []
    print("Active scenarios:", ", ".join(ids) if ids else "none")
    pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    for p in pods:
        name = p["metadata"]["name"]
        statuses = p["status"].get("containerStatuses", [])
        ready = sum(c["ready"] for c in statuses)
        restarts = sum(c["restartCount"] for c in statuses)
        waiting = [c["state"]["waiting"]["reason"] for c in statuses if "waiting" in c["state"]]
        if p["metadata"].get("deletionTimestamp"):
            state, flag = "Terminating", ""
        else:
            state = waiting[0] if waiting else p["status"]["phase"]
            flag = "" if (ready == len(statuses) and not waiting) else "   <-- problem"
        print(f"  {name:<45} {ready}/{len(statuses)}  {state:<18} restarts={restarts}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Break Online Boutique on purpose, and restore it.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show all scenarios").set_defaults(func=cmd_list)
    b = sub.add_parser("break", help="inject a failure")
    b.add_argument("scenario")
    b.add_argument("--allow-multiple", action="store_true", help="allow several active scenarios at once")
    b.set_defaults(func=cmd_break)
    r = sub.add_parser("restore", help="undo a failure")
    r.add_argument("scenario", nargs="?")
    r.add_argument("--all", action="store_true")
    r.add_argument("--no-wait", action="store_true", help="don't wait for the rollout to finish")
    r.set_defaults(func=cmd_restore)
    sub.add_parser("status", help="show active scenarios and pod health").set_defaults(func=cmd_status)
    args = ap.parse_args()
    try:
        args.func(args)
    except RuntimeError as e:
        sys.exit(f"kubectl failed:\n{e}")


if __name__ == "__main__":
    main()
