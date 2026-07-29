import { getJSON } from "../api.js";
import { el } from "../dom.js";
import { startPolling } from "../poller.js";

const JOB_POLL_MS = 5000;
const HOST_POLL_MS = 15000;

function isPresent(value) {
  return value !== null && value !== undefined && value !== "";
}

function metric(value, formatter = (v) => String(v)) {
  return isPresent(value) ? formatter(value) : "";
}

function dotClass(kind) {
  return {
    ok: "dot-ok",
    warn: "dot-warn",
    err: "dot-err",
    muted: "dot-muted",
  }[kind] || "dot-muted";
}

function statusDot(kind, text) {
  return el("span", { class: "cluster row-status" }, [
    el("span", { class: `dot ${dotClass(kind)}` }),
    el("span", { text }),
  ]);
}

function checkRow(kind, name, detail) {
  return el("div", { class: "check-row" }, [
    el("span", { class: `dot ${dotClass(kind)}` }),
    el("strong", { text: name }),
    detail instanceof Node ? detail : el("span", { text: detail }),
  ]);
}

function statusKind(status) {
  if (status === "conformant" || status === "succeeded" || status === "OK" || status === "IN_SYNC" || status === "MATCH") return "ok";
  if (status === "legacy" || status === "in_progress" || status === "WARN" || status === "SKIP") return "warn";
  if (status === "incomplete" || status === "failed" || status === "crashed" || status === "FAIL") return "err";
  return "muted";
}

function elapsedText(startedUtc) {
  const start = Date.parse(startedUtc || "");
  if (!Number.isFinite(start)) return "elapsed unavailable";
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const mins = Math.floor(seconds / 60);
  const hrs = Math.floor(mins / 60);
  return hrs ? `${hrs}h ${mins % 60}m` : `${mins}m ${seconds % 60}s`;
}

function runMeta(run) {
  return [run.scenario_name, run.algorithm, run.gnss_state]
    .filter(isPresent)
    .map((value) => el("span", { text: value }));
}

function runRow(run) {
  const meta = runMeta(run);
  const main = [el("div", { class: "row-main", text: run.run_id })];
  if (meta.length) main.push(el("div", { class: "row-meta" }, meta));
  const status = run.contract_status || "unknown";
  const horiz = run.key_metrics?.horizontal_error_max_m;
  const tail = [statusDot(statusKind(status), status)];
  if (horiz !== null && horiz !== undefined) {
    tail.push(el("span", { class: "row-metric", text: `${horiz.toFixed(3)} m` }));
  }
  tail.push(el("span", { class: "row-chevron", text: ">" }));
  return el("a", { class: "list-row", href: `/runs/${encodeURIComponent(run.run_id)}` }, [
    el("div", {}, main),
    el("div", { class: "cluster" }, tail),
  ]);
}

function latestRuns(runs) {
  return [...runs].sort((a, b) => {
    const av = Date.parse(a.last_modified_utc || a.created_utc || "") || 0;
    const bv = Date.parse(b.last_modified_utc || b.created_utc || "") || 0;
    if (av !== bv) return bv - av;
    return String(b.run_id || "").localeCompare(String(a.run_id || ""));
  }).slice(0, 8);
}

function deploymentBuildRow(deployment) {
  for (const items of Object.values(deployment?.groups || {})) {
    for (const item of items || []) {
      if (item?.name === "px4 build up to date") {
        return checkRow(statusKind(item.status), item.name, item.detail || item.status || "not reported");
      }
    }
  }
  return checkRow("muted", "px4 build up to date", "not reported");
}

function modelSyncRow(modelSync) {
  const rows = [...(modelSync?.model_sync || []), ...(modelSync?.fov_consistency || [])];
  if (!rows.length) return checkRow("muted", "model-sync", "no cached results returned");
  const failed = rows.filter((row) => !["IN_SYNC", "MATCH"].includes(row.status)).length;
  const ok = rows.length - failed;
  return checkRow(failed ? "err" : "ok", "model-sync", `${ok} OK, ${failed} FAIL`);
}

function vizRow(host, port) {
  const value = host?.viz?.[port];
  if (value === true) return checkRow("ok", `gz-web ${port}`, "listening");
  if (value === false) return checkRow("err", `gz-web ${port}`, "not listening");
  return checkRow("muted", `gz-web ${port}`, "not reported");
}

function renderRunsTile(runs) {
  const counts = {
    conformant: runs.filter((run) => run.contract_status === "conformant").length,
    legacy: runs.filter((run) => run.contract_status === "legacy").length,
  };
  counts.incomplete = runs.length - counts.conformant - counts.legacy;
  return el("a", { class: "tile tile-link stack", href: "/runs" }, [
    el("span", { class: "label", text: "Total runs" }),
    el("strong", { text: String(runs.length) }),
    el("span", {
      class: "sub",
      text: `${counts.conformant} conformant · ${counts.incomplete} incomplete · ${counts.legacy} legacy`,
    }),
  ]);
}

function renderJobTile(job) {
  if (!job) {
    return el("div", { class: "tile stack" }, [
      el("span", { class: "label", text: "Active job" }),
      el("strong", { text: "idle" }),
      el("span", { class: "sub", text: "No dashboard job is running." }),
    ]);
  }
  const meta = [job.scenario, elapsedText(job.started_utc)].filter(isPresent).map((value) => el("span", { text: value }));
  return el("a", { class: "tile tile-link stack", href: `/jobs/${encodeURIComponent(job.job_id)}` }, [
    el("span", { class: "label", text: "Active job" }),
    el("strong", { class: "tile-id", text: job.job_id }),
    el("span", { class: "row-meta" }, meta),
  ]);
}

function renderHostTile(host) {
  const memKind = !host ? "muted" : host.mem_ok === true ? "ok" : host.mem_ok === false ? "err" : "warn";
  const diskKind = !host ? "muted" : host.disk_ok !== true ? "err" : host.disk_warn ? "warn" : "ok";
  return el("div", { class: "tile stack" }, [
    el("span", { class: "label", text: "Host" }),
    checkRow(memKind, "Memory", metric(host?.mem_available_mb, (v) => `${v} MB free; guard ${host.mem_guard_mb} MB`) || "not reported"),
    checkRow(diskKind, "Disk", metric(host?.disk_free_gb, (v) => `${v} GB free; guard ${host.disk_block_gb} GB`) || "not reported"),
  ]);
}

function renderDeploymentTile(deployment) {
  const summary = deployment?.summary || {};
  const ok = summary.ok || 0;
  const fail = summary.fail || 0;
  const warn = summary.warn || 0;
  return el("a", { class: "tile tile-link stack", href: "/health" }, [
    el("span", { class: "label", text: "Deployment" }),
    el("strong", { text: `${ok} OK / ${fail} FAIL` }),
    warn ? el("span", { class: "sub", text: `${warn} WARN` }) : el("span", { class: "sub", text: "Deployment cache loaded." }),
  ]);
}

function renderRecentRuns(runs) {
  const list = el("div", { class: "list" });
  for (const run of latestRuns(runs)) list.appendChild(runRow(run));
  if (!list.children.length) return el("p", { class: "empty", text: "No runs have been indexed yet." });
  list.appendChild(el("a", { class: "list-row", href: "/runs" }, [
    el("div", { class: "row-main", text: "View all runs" }),
    el("span", { class: "row-chevron", text: ">" }),
  ]));
  return list;
}

function renderSystem(host, modelSync, deployment) {
  return el("div", { class: "list" }, [
    vizRow(host, "9002"),
    vizRow(host, "9003"),
    modelSyncRow(modelSync),
    deploymentBuildRow(deployment),
  ]);
}

export async function renderOverview() {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));

  let runs = [];
  let deployment = null;
  let modelSync = null;
  let activeJob = null;
  let host = null;

  const tiles = el("section", { class: "grid-auto overview-tiles" });
  const lower = el("section", { class: "overview-columns" });
  const recentHost = el("section", { class: "stack" });
  const systemHost = el("section", { class: "stack" });

  function renderStatic() {
    tiles.replaceChildren(
      renderRunsTile(runs),
      renderJobTile(activeJob),
      renderHostTile(host),
      renderDeploymentTile(deployment),
    );
    recentHost.replaceChildren(el("h2", { text: "Recent runs" }), renderRecentRuns(runs));
    systemHost.replaceChildren(el("h2", { text: "Host & system" }), renderSystem(host, modelSync, deployment));
  }

  try {
    [runs, deployment, modelSync] = await Promise.all([
      getJSON("/api/runs"),
      getJSON("/api/checks/deployment"),
      getJSON("/api/checks/model_sync"),
    ]);
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load overview: " + ((e && e.message) || e) }));
    return;
  }

  lower.replaceChildren(recentHost, systemHost);
  app.replaceChildren(
    el("div", { class: "cluster overview-title" }, [
      el("h1", { text: "Overview" }),
      el("a", { class: "btn-ghost", href: "/runs", text: "Open runs" }),
    ]),
    tiles,
    lower,
  );
  renderStatic();

  startPolling(async () => {
    const data = await getJSON("/api/jobs");
    activeJob = data.active || null;
    renderStatic();
  }, { baseMs: JOB_POLL_MS, maxMs: 30000 });

  startPolling(async () => {
    host = await getJSON("/api/host");
    renderStatic();
  }, { baseMs: HOST_POLL_MS, maxMs: 30000 });
}
