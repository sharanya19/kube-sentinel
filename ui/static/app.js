// Kube-Sentinel approval console. Plain JS, no build step.
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STATUS = {
  pending_approval: ["Waiting for approval", "warn"],
  fixing: ["Applying fix…", "info"],
  resolved: ["Resolved", "ok"],
  fix_failed: ["Fix failed", "bad"],
  rejected: ["Rejected", "muted"],
  investigating: ["Investigating…", "info"],
  failed: ["Investigation failed", "bad"],
};

let reports = [];
let running = [];
let selected = location.hash.slice(1) || null;
let previews = {}; // report id -> dry-run result

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

function pill(status) {
  const [label, cls] = STATUS[status] || [status, "muted"];
  return `<span class="pill ${cls}">${esc(label)}</span>`;
}

// ---------------- data ----------------

async function refresh() {
  try {
    const [state, reps] = await Promise.all([api("/api/state"), api("/api/reports")]);
    running = state.running;
    reports = reps;
    renderHealth(state);
    renderList();
    renderDetail();
  } catch (e) {
    $("#health").className = "pill bad";
    $("#health").textContent = "UI server not reachable";
  }
}

function renderHealth({ cluster, watch }) {
  const h = $("#health");
  if (cluster.ok === null) {
    h.className = "pill bad"; h.textContent = "Cluster unreachable"; h.title = cluster.error;
  } else if (cluster.ok) {
    h.className = "pill ok"; h.textContent = "Shop healthy"; h.title = "";
  } else {
    const n = cluster.unhealthy_pods.length + cluster.unhealthy_deployments.length;
    h.className = "pill bad"; h.textContent = `${n} problem${n === 1 ? "" : "s"} in the shop`;
    h.title = [...cluster.unhealthy_deployments, ...cluster.unhealthy_pods].join("\n");
  }
  const w = $("#watch");
  w.hidden = !watch.enabled;
  if (watch.enabled) {
    w.className = watch.error ? "pill bad" : "pill info";
    w.textContent = watch.error ? "Alert watch error" : "Watching alerts";
    w.title = watch.error || `Last check ${ago(watch.last_check)}`;
  }
  $("#investigate-btn").disabled = running.some((r) => !r.error);
}

// ---------------- list ----------------

let lastListKey = "";

function renderList() {
  const filter = $("#filter").value;
  const items = [
    ...running.map((r) => ({ id: r.id, status: r.error ? "failed" : "investigating",
      title: r.source === "alertmanager" ? "Alert fired" : "Manual investigation",
      sub: r.alert, time: r.started_at })),
    ...reports.filter((r) => filter === "all" || r.status === filter).map((r) => ({
      id: r.id, status: r.status, title: r.report.component, sub: r.report.summary, time: r.created_at })),
  ];
  // Redraw only when something changed: replacing the list mid-click would swallow the click.
  const key = JSON.stringify([items, selected, Math.floor(Date.now() / 60000)]);
  if (key === lastListKey) return;
  lastListKey = key;
  $("#list").innerHTML = items.length ? items.map((i) => `
    <li class="item ${i.id === selected ? "active" : ""}" data-id="${esc(i.id)}">
      <div class="item-top"><span class="item-title">${esc(i.title)}</span>${pill(i.status)}</div>
      <div class="item-sub">${esc(i.sub)}</div>
      <div class="item-time">${ago(i.time)}</div>
    </li>`).join("") : `<li class="hint" style="padding:8px 12px">No incidents yet.</li>`;
}

$("#list").addEventListener("click", (e) => {
  const li = e.target.closest(".item");
  if (!li) return;
  selected = li.dataset.id;
  location.hash = selected;
  renderList();
  renderDetail();
});
$("#filter").addEventListener("change", renderList);

// ---------------- detail ----------------

function traceList(trace) {
  return `<ol class="trace">${trace.map((t) =>
    `<li><code>${esc(t.tool)}(${esc(Object.keys(t.args || {}).length ? JSON.stringify(t.args) : "")})</code></li>`).join("")}</ol>`;
}

let lastDetailKey = "";

function renderDetail() {
  const d = $("#detail");
  const run = running.find((r) => r.id === selected);
  const r = run ? null : reports.find((x) => x.id === selected);
  // Only redraw when something changed, so open <details> sections stay open between polls.
  const key = JSON.stringify([run, r, r && previews[r.id], r && STATUS[r.status] && r.status === "fixing" ? Math.floor(Date.now() / 30000) : 0]);
  if (key === lastDetailKey) return;
  lastDetailKey = key;
  if (run) return renderRunning(d, run);
  if (!r) return; // keep the empty state

  const rep = r.report;
  const fix = rep.suggested_fix;
  d.innerHTML = `
    <div class="head">
      <h1>${esc(rep.summary)}</h1>
      ${pill(r.status)}
    </div>
    <div class="meta">
      ${esc(ago(r.created_at))} · ${r.source === "alertmanager" ? "triggered by Alertmanager" : "started manually"}
      · ${r.tool_calls} tool calls in ${r.seconds}s · ${esc(r.model)}
    </div>

    <div class="card fix">
      <h3>Suggested fix</h3>
      <pre>${esc(fix.command)}</pre>
      <p class="why">${esc(fix.reason)}</p>
      <div id="fix-area">${fixArea(r)}</div>
    </div>

    <div class="card">
      <h3>Diagnosis</h3>
      <dl class="kv">
        <dt>Root cause</dt><dd>${esc(rep.root_cause)}</dd>
        <dt>Component</dt><dd><code>${esc(rep.component)}</code> · ${esc(rep.category)}</dd>
        <dt>User impact</dt><dd>${esc(rep.user_impact)}</dd>
        <dt>Confidence</dt><dd>${esc(rep.confidence)}</dd>
      </dl>
    </div>

    <div class="card">
      <h3>Evidence</h3>
      <ul class="evidence">${rep.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>
    </div>

    <div class="card">
      <details>
        <summary>How the agent investigated (${r.trace.length} tool calls)</summary>
        ${traceList(r.trace)}
      </details>
      <details style="margin-top:8px">
        <summary>Original alert</summary>
        <pre style="white-space:pre-wrap">${esc(r.alert)}</pre>
      </details>
    </div>`;
  bindFixButtons(r);
}

function fixArea(r) {
  const fix = r.report.suggested_fix;
  if (r.status === "pending_approval") {
    if (fix.action === "none") {
      return `<div class="outcome muted">No automatic fix: this one needs a human.</div>`;
    }
    const p = previews[r.id];
    return `
      <div class="actions">
        <button class="btn" id="preview-btn">Preview (dry run)</button>
        <button class="btn approve" id="approve-btn">Approve &amp; run fix</button>
        <button class="btn ghost" id="reject-btn">Reject</button>
      </div>
      ${p ? previewBox(p) : ""}`;
  }
  if (r.status === "fixing") {
    return `<div class="outcome warn live"><span class="spinner"></span>
      Fix approved ${esc(ago(r.decision?.at))}. Running it and waiting for the deployment to recover…</div>`;
  }
  if (r.status === "resolved") {
    return `<div class="outcome ok">✓ Fix applied after approval and verified: <code>${esc(fix.deployment)}</code>
      is healthy again (${r.verification?.seconds ?? "?"}s to recover).</div>`;
  }
  if (r.status === "fix_failed") {
    const why = r.fix_error || `deployment still '${r.verification?.state}' after ${r.verification?.seconds}s`;
    return `<div class="outcome bad">✗ The fix did not resolve the incident: ${esc(why)}</div>`;
  }
  if (r.status === "rejected") {
    return `<div class="outcome muted">Rejected ${esc(ago(r.decision?.at))}. Nothing was changed.
      ${r.decision?.reason ? `Reason: ${esc(r.decision.reason)}` : ""}</div>`;
  }
  return "";
}

function previewBox(p) {
  if (p.error) return `<div class="preview">Preview failed: ${esc(p.error)}</div>`;
  let what = "";
  if (p.action === "scale") what = `Scale <code>${esc(p.deployment)}</code> from ${p.from} to ${p.to} replicas.`;
  else if (p.action === "rollout_undo") what = `Roll <code>${esc(p.deployment)}</code> back from revision ${esc(p.from_revision)}
      to ${esc(p.to_revision)}. This reverts:<ul>${(p.reverts || []).map((c) => `<li><code>${esc(c)}</code></li>`).join("")}</ul>`;
  else if (p.action === "rollout_restart") what = `Restart all pods of <code>${esc(p.deployment)}</code>.`;
  return `<div class="preview"><b>Dry run, nothing changed yet.</b> ${what}</div>`;
}

function bindFixButtons(r) {
  const prev = $("#preview-btn"), ok = $("#approve-btn"), no = $("#reject-btn");
  if (prev) prev.onclick = async () => {
    prev.disabled = true; prev.textContent = "Checking…";
    try { previews[r.id] = await api(`/api/reports/${r.id}/preview`, { method: "POST" }); }
    catch (e) { previews[r.id] = { error: e.message }; }
    renderDetail();
  };
  if (ok) ok.onclick = () => {
    $("#approve-command").textContent = r.report.suggested_fix.command;
    $("#approve-dialog").showModal();
    $("#approve-dialog").onclose = async () => {
      if ($("#approve-dialog").returnValue !== "go") return;
      ok.disabled = true;
      try { await api(`/api/reports/${r.id}/approve`, { method: "POST" }); } catch (e) { alert(e.message); }
      refresh();
    };
  };
  if (no) no.onclick = () => {
    $("#reject-reason").value = "";
    $("#reject-dialog").showModal();
    $("#reject-dialog").onclose = async () => {
      if ($("#reject-dialog").returnValue !== "go") return;
      try {
        await api(`/api/reports/${r.id}/reject`, { method: "POST",
          body: JSON.stringify({ reason: $("#reject-reason").value || null }) });
      } catch (e) { alert(e.message); }
      refresh();
    };
  };
}

function renderRunning(d, run) {
  d.innerHTML = `
    <div class="head"><h1>${run.error ? "Investigation failed" : "Investigating…"}</h1>
      ${pill(run.error ? "failed" : "investigating")}</div>
    <div class="meta">started ${esc(ago(run.started_at))} · ${run.source === "alertmanager" ? "triggered by Alertmanager" : "started manually"}</div>
    ${run.error ? `<div class="card"><div class="outcome bad">${esc(run.error)}</div>
        <div class="actions" style="margin-top:10px"><button class="btn" id="dismiss-btn">Dismiss</button></div></div>` : ""}
    <div class="card">
      <h3>Alert</h3><pre style="white-space:pre-wrap;margin:0">${esc(run.alert)}</pre>
    </div>
    <div class="card">
      <h3>${run.error ? "Tool calls" : `<span class="live"><span class="spinner"></span> Tool calls so far</span>`}</h3>
      ${run.trace.length ? traceList(run.trace) : `<p class="hint">Waiting for the first tool call…</p>`}
      ${run.model_switches.length ? `<p class="hint">Model switches: ${esc(run.model_switches.join("; "))}</p>` : ""}
    </div>`;
  const dis = $("#dismiss-btn");
  if (dis) dis.onclick = async () => {
    await api(`/api/running/${run.id}`, { method: "DELETE" });
    selected = null; location.hash = ""; refresh();
  };
}

// ---------------- investigate ----------------

$("#investigate-btn").onclick = () => $("#investigate-dialog").showModal();
$("#investigate-dialog").addEventListener("close", async () => {
  if ($("#investigate-dialog").returnValue !== "go") return;
  try {
    const { id } = await api("/api/investigate", { method: "POST",
      body: JSON.stringify({ alert: $("#alert-text").value }) });
    selected = id; location.hash = id;
  } catch (e) { alert(e.message); }
  refresh();
});

// When a run finishes, jump to its report.
let lastRunning = new Set();
setInterval(async () => {
  await refresh();
  const now = new Set(running.map((r) => r.id));
  if (selected && lastRunning.has(selected) && !now.has(selected) && reports[0]) {
    selected = reports[0].id; location.hash = selected; renderList(); renderDetail();
  }
  lastRunning = now;
}, 3000);
refresh();
