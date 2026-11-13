# Kube-Sentinel Approval UI (`ui`)

## Overview
Local FastAPI web backend and human-in-the-loop remediation gateway for Kube-Sentinel. It serves the single-page approval console, persists and updates shared JSON incident reports on disk, runs manual or Alertmanager-triggered agent investigations in the background, and acts as the sole component in Kube-Sentinel permitted to invoke cluster-mutating MCP actions—only after explicit human approval—followed by automated deployment recovery verification.

## Interface
- `main()`: CLI entry point (`python -m ui`) supporting `--watch` (`[__main__.py:--watch]`, sets `KUBE_SENTINEL_UI_WATCH=1`) and `--port` (`[__main__.py:--port]`, default `8000`) via `uvicorn.run` (`[__main__.py:main]`).
- `app` / `lifespan(app)`: FastAPI application mounting `/static` (`[server.py:STATIC]`) and managing the optional `watch_loop()` background task when `KUBE_SENTINEL_UI_WATCH == "1"` (`[server.py:app]`, `[server.py:lifespan]`).
- HTTP Endpoints (`server.py`):
  - `GET /` (`[server.py:index]`): Serves `static/index.html`.
  - `GET /api/state` (`[server.py:state]`): Returns live cluster health (`[server.py:_cluster_health]`), active `running` investigations, and Alertmanager `WATCH` state.
  - `GET /api/reports` (`[server.py:reports]`) & `GET /api/reports/{report_id}` (`[server.py:report]`): Lists all stored incident reports or returns one report by ID.
  - `POST /api/reports/{report_id}/preview` (`[server.py:preview]`): Runs `fixer.call_action(fix, dry_run=True)` without modifying the cluster.
  - `POST /api/reports/{report_id}/approve` (`[server.py:approve]`): Transitions a `pending_approval` report to `fixing`, executes the allowlisted MCP action asynchronously, and verifies recovery into `resolved` or `fix_failed`.
  - `POST /api/reports/{report_id}/reject` (`[server.py:reject]`): Accepts `Decision` (`[server.py:Decision]`) and transitions a `pending_approval` report to `rejected`.
  - `POST /api/investigate` (`[server.py:investigate]`): Accepts `InvestigateRequest` (`[server.py:InvestigateRequest]`) and launches `start_investigation(alert, "ui")` (`[server.py:start_investigation]`).
  - `DELETE /api/running/{temp_id}` (`[server.py:dismiss_failed]`): Clears a failed run entry from `running`.
- `ACTIONS` / `VERIFY_TIMEOUT` / `call_action(fix, dry_run)` / `verify(deployment)`: Allowlisted MCP action dispatcher (`scale_deployment`, `rollout_undo`, `rollout_restart`) and 240-second post-fix Kubernetes health verifier (`[fixer.py:ACTIONS]`, `[fixer.py:VERIFY_TIMEOUT]`, `[fixer.py:call_action]`, `[fixer.py:verify]`).
- `now()` / `list_reports()` / `get(report_id)` / `save(report)` / `update(report_id, **fields)`: Thread-safe JSON file store backed by `agent.run.REPORTS_DIR` (`[store.py:now]`, `[store.py:list_reports]`, `[store.py:get]`, `[store.py:save]`, `[store.py:update]`).
- Subdirectories:
  - [`static`](./static/EGM.md): Plain-JavaScript single-page approval console (`index.html`, `app.js`) providing 3-second state/report polling (`refresh`), XSS-escaped rendering (`esc`), dry-run diff previews (`previewBox`), and modal-confirmed approve/reject/investigate workflows (`bindFixButtons`).

## Invariants
- `uvicorn.run` binds strictly to `127.0.0.1` so cluster-mutating HTTP routes are never reachable from remote hosts (`[__main__.py:main]`).
- Only reports in `status == "pending_approval"` may be approved or rejected, and `_busy` prevents concurrent or duplicate approval of the same `report_id` (returning HTTP 409) (`[server.py:approve]`, `[server.py:reject]`, `[server.py:_busy]`).
- `fixer._args` and `server.approve` restrict remediation strictly to `ACTIONS = {"scale_deployment", "rollout_undo", "rollout_restart"}` targeting a non-empty `deployment` in namespace `"default"` (`[fixer.py:ACTIONS]`, `[fixer.py:_args]`, `[server.py:approve]`).
- `store._path` enforces `report_id.replace("-", "").isalnum()` to block path traversal (`[store.py:_path]`), and `save`/`update` serialize file writes with `_lock` (`[store.py:_lock]`, `[store.py:save]`, `[store.py:update]`).
- At most one non-errored investigation may run at a time: `POST /api/investigate` raises HTTP 409 and `watch_loop()` defers new Alertmanager runs while `any(not r.get("error") for r in running.values())` (`[server.py:investigate]`, `[server.py:watch_loop]`), and `dismiss_failed` deletes an entry only if `entry.get("error")` is truthy (`[server.py:dismiss_failed]`).

## Side Effects
- Binds a local HTTP listener on `127.0.0.1:<port>` and sets `KUBE_SENTINEL_UI_WATCH="1"` in `os.environ` when `--watch` is supplied (`[__main__.py:main]`).
- Creates `REPORTS_DIR` (`reports/`) if absent and reads/writes `reports/<report_id>.json` files (`[store.py:list_reports]`, `[store.py:get]`, `[store.py:save]`, `[store.py:update]`).
- Spawns `python -m mcp_server` subprocesses over stdio with `KUBE_SENTINEL_ALLOW_ACTIONS="true"` to preview (`dry_run=True`) or apply (`dry_run=False`) Kubernetes deployment changes (`[fixer.py:call_action]`).
- Queries the Kubernetes API via `mcp_server.k8s` (`list_pods`, `list_deployments`, `describe_deployment`) for live cluster health and 5-second recovery polling up to `VERIFY_TIMEOUT` (`240`s) (`[server.py:_cluster_health]`, `[fixer.py:verify]`).
- Spawns background `asyncio` tasks that run Gemini/LangGraph investigations (`agent.run.run_investigation`) and poll Alertmanager (`agent.watch.shop_alerts`) (`[server.py:start_investigation]`, `[server.py:watch_loop]`).

## Verification
- Run: `<test-command>`
