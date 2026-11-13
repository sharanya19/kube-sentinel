# Approval UI

A small local web app where a human reviews the agent's incident reports and decides what happens:
**Preview** the fix (dry run), **Approve** it (it runs, then recovery is verified), or **Reject** it.
This is the only part of Kube-Sentinel that changes the cluster, and only after a click.

```bash
python -m ui              # http://127.0.0.1:8000
python -m ui --watch      # also investigate automatically when shop alerts fire
```

## What's on the page

- **Shop health** in the header: live, from the cluster (e.g. "1 problem in the shop").
- **Incidents** on the left, newest first, with a status (see the lifecycle below).
- **A report**: summary, the suggested fix as a command, diagnosis (root cause, component, user impact,
  confidence), evidence, and every tool call the agent made.
- **Investigate now**: start an investigation from a symptom you type; tool calls appear live.

## Incident lifecycle

```
Investigating -> No incident   (nothing wrong found: nothing to approve, kept for the record)
Investigating -> Waiting for approval -> Applying fix -> Resolved
                       |                       '------> Fix failed (applied, still unhealthy;
                       |                                 or couldn't be applied: nothing changed)
                       |-- Reject --------------------> Rejected
                       |-- cluster changed since -----> Blocked: cluster changed   (Re-investigate)
                       '-- problem already gone ------> Expired                    (Re-investigate)
```

All incidents are **kept for history**, whatever their status; nothing is deleted.

## Stale-fix guard

A report is a snapshot: its fix was right when the agent wrote it. If the deployment changed afterwards,
running the old fix can be useless or harmful (a "roll back one revision" after someone deployed a newer
fix would re-deploy the broken version). So [`agent/guard.py`](../agent/guard.py):

1. **Records the target deployment's state** when the report is written: a fingerprint of its pod
   template, its revision and replica count.
2. **Re-checks it when Approve is clicked**, then **dry-runs** the action. If the deployment changed, the
   problem is already gone, or the dry run fails, the fix does **not** run and the report becomes
   *Blocked* or *Expired* with the reason.
3. **Expires waiting reports older than 60 minutes** (`KUBE_SENTINEL_EXPIRE_MINUTES`) whose problem is
   gone or whose deployment changed. They stay in history but can't be approved.

Tested against the live cluster: a report written during `bad-config` and approved after a restore was
**blocked** ("checkoutservice was redeployed after this report (revision 18 -> 19)") with the revision
untouched; the same report approved while still broken ran and **resolved** it; a "scale redis-cart"
report approved after Redis was back **expired** ("already scaled back to 1 replica").

## What happens when you click Approve

```mermaid
sequenceDiagram
    actor H as Human
    participant UI as Approval UI
    participant MCP as MCP server (actions enabled)
    participant K as Kubernetes
    H->>UI: Preview (dry run)
    UI->>MCP: rollout_undo(dry_run=true)
    MCP-->>UI: "would revert PAYMENT_SERVICE_ADDR 50052 -> 50051"
    H->>UI: Approve -> confirm
    UI->>MCP: rollout_undo(dry_run=false)
    MCP->>K: patch deployment
    loop every 5s, up to 4 min
        UI->>K: is the deployment healthy?
    end
    UI-->>H: Resolved (healthy again in 5-10s) / Fix failed
```

## Safety

- Listens on **127.0.0.1 only**: it can change the cluster, so other machines can't reach it.
- Fixes go through the **MCP server's allowlist**: 3 actions, allowed namespaces only, replicas capped.
  The UI can't run arbitrary commands.
- Only reports **waiting for approval** can be approved, and only once (a second click gets HTTP 409).
- A fix only runs if the cluster still matches the report and a dry run succeeds (stale-fix guard above).
- Every decision is recorded in the report: approved/rejected, when, the reason, what ran, and whether
  the deployment recovered.

## Storage

Reports live in Postgres (`incidents` table, next to the runbooks), shared with the agent CLI and the in-cluster
deployment. Approve and Reject are single conditional updates, so a fix can't be approved twice, even from two
tabs or two replicas. `KUBE_SENTINEL_STORE=files` keeps them as JSON files in `reports/` instead (no database).

| File | Role |
|---|---|
| [`server.py`](server.py) | FastAPI app: reports API, approve / reject / preview, live investigations, optional alert watch |
| [`fixer.py`](fixer.py) | Runs the approved action over MCP, then waits for the deployment to be healthy |
| [`../agent/store.py`](../agent/store.py) | Incident storage (Postgres, or JSON files as a fallback) |
| [`static/`](static/) | The page: plain HTML, CSS and JavaScript, no build step |
