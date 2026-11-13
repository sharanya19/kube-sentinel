# Kube-Sentinel Approval Console Frontend (`ui/static`)

## Overview
Plain-JavaScript, zero-build single-page frontend for the Kube-Sentinel human-in-the-loop approval console. It polls backend cluster/watch health and incident state, displays live LangGraph investigation tool traces and structured incident reports, and gates cluster remediation behind dry-run previews and explicit modal confirmation dialogs.

## Interface
- `index.html`: Single-page application shell defining status pills (`[index.html:watch]`, `[index.html:health]`), manual trigger button (`[index.html:investigate-btn]`), incident filter and sidebar list (`[index.html:filter]`, `[index.html:list]`), report detail container (`[index.html:detail]`), confirmation modals (`[index.html:investigate-dialog]`, `[index.html:approve-dialog]`, `[index.html:reject-dialog]`), and script entry (`[index.html:script]`).
- `STATUS`: Status-to-badge label and CSS class map covering `pending_approval`, `fixing`, `resolved`, `fix_failed`, `rejected`, `investigating`, and `failed` (`[app.js:STATUS]`).
- `api(path, opts)`: Asynchronous `fetch` wrapper setting `Content-Type: application/json` and raising `Error(body.detail || res.statusText)` on non-2xx responses (`[app.js:api]`).
- `refresh()`: Polls `/api/state` and `/api/reports` concurrently and orchestrates `renderHealth`, `renderList`, and `renderDetail` (`[app.js:refresh]`).
- `renderHealth({ cluster, watch })`: Updates cluster health pill, Alertmanager watch status pill, and `#investigate-btn` disabled state (`[app.js:renderHealth]`).
- `renderList()`: Renders running investigations and status-filtered incident reports into `#list` (`[app.js:renderList]`).
- `renderDetail()` / `renderRunning(d, run)`: Renders either an in-progress/failed investigation trace or a completed incident diagnosis, evidence list, tool-call trace (`[app.js:traceList]`), and remediation controls (`[app.js:renderDetail]`, `[app.js:renderRunning]`).
- `fixArea(r)` / `previewBox(p)` / `bindFixButtons(r)`: Renders lifecycle-specific remediation controls, dry-run diff summaries (`scale`, `rollout_undo`, `rollout_restart`), and modal-confirmed preview/approve/reject handlers (`[app.js:fixArea]`, `[app.js:previewBox]`, `[app.js:bindFixButtons]`).
- `$` / `esc(s)` / `ago(iso)` / `pill(status)`: DOM query selector helper, HTML entity escaper, relative time formatter, and status pill renderer (`[app.js:$]`, `[app.js:esc]`, `[app.js:ago]`, `[app.js:pill]`).

## Invariants
- All dynamic strings interpolated into `innerHTML` templates must be sanitized with `esc(s)` (`[app.js:esc]`).
- `renderList()` and `renderDetail()` skip `innerHTML` replacement when serialized render keys match `lastListKey` or `lastDetailKey`, preserving in-flight clicks and expanded `<details>` sections across 3-second polling ticks (`[app.js:lastListKey]`, `[app.js:lastDetailKey]`, `[app.js:renderList]`, `[app.js:renderDetail]`).
- Remediation buttons only render when `r.status === "pending_approval"` and `fix.action !== "none"` (`[app.js:fixArea]`), and both approval and rejection require modal confirmation with `returnValue === "go"` (`[app.js:bindFixButtons]`, `[index.html:approve-dialog]`, `[index.html:reject-dialog]`).
- `#investigate-btn` remains disabled whenever any active run in `running` has no error (`running.some((r) => !r.error)`) (`[app.js:renderHealth]`).

## Side Effects
- Executes `GET /api/state` and `GET /api/reports` on initial load and every `3000` ms via `setInterval` (`[app.js:refresh]`, `[app.js:api]`).
- Sends `POST /api/investigate`, `POST /api/reports/{id}/preview`, `POST /api/reports/{id}/approve`, `POST /api/reports/{id}/reject`, and `DELETE /api/running/{id}` requests in response to user actions (`[app.js:bindFixButtons]`, `[app.js:renderRunning]`, `[app.js:api]`).
- Mutates browser DOM state, opens native modal `<dialog>` elements via `showModal()`, invokes `alert()` on action errors, and synchronizes selected incident/run IDs with `location.hash` (`[app.js:selected]`, `[app.js:renderList]`, `[app.js:bindFixButtons]`).

## Verification
- Run: `<test-command>`
