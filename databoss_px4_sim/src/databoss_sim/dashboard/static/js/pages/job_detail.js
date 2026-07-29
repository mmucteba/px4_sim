import { getJSON, postJSON } from "../api.js";
import { el } from "../dom.js";
import { startPolling } from "../poller.js";

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "crashed"]);
const PROXY_CMD = "venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py --listen-port 9002 --upstream ws://127.0.0.1:9003";
const COARSE_PHASES = ["queued", "running", "finished"];
const RICH_PHASES = ["world ready", "PX4 up", "armed", "airborne", "hovering", "landing", "analysing"];
const PHASE_ALIASES = new Map([
  ["worldready", "world ready"],
  ["world", "world ready"],
  ["px4up", "PX4 up"],
  ["px4", "PX4 up"],
  ["armed", "armed"],
  ["airborne", "airborne"],
  ["takeoff", "airborne"],
  ["hovering", "hovering"],
  ["hover", "hovering"],
  ["landing", "landing"],
  ["land", "landing"],
  ["analysing", "analysing"],
  ["analyzing", "analysing"],
  ["analysis", "analysing"],
]);

function statusBadge(status) {
  return el("span", { class: `badge status-${status || "unknown"}`, text: status || "unknown" });
}

function runIdFromDir(runDir) {
  return runDir ? String(runDir).split("/").filter(Boolean).pop() : "";
}

function runDirLink(job) {
  const runId = runIdFromDir(job.run_dir);
  return runId ? el("a", { href: `/artifacts/runs/${encodeURIComponent(runId)}/`, text: runId }) : el("span", { class: "help", text: "run_dir pending" });
}

function returncodeText(job) {
  if (job.returncode === 143 || job.returncode === 130) return `${job.returncode} (cancelled by signal)`;
  return job.returncode;
}

async function fetchText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.text();
}

async function readRunStatus(runId) {
  if (!runId) return null;
  for (const path of ["end_to_end_status.json", "logs/pxh_takeoff_land_truth_status.json"]) {
    const r = await fetch(`/artifacts/runs/${encodeURIComponent(runId)}/${path}`);
    if (r.ok) return r.json();
  }
  return null;
}

function terminal(job) {
  return TERMINAL.has(job.status);
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "-";
  const total = Math.max(0, Math.floor(seconds));
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return hrs ? `${hrs}h ${mins}m ${secs}s` : `${mins}m ${secs}s`;
}

function elapsedText(job) {
  const start = Date.parse(job.started_utc || "");
  if (!Number.isFinite(start)) return "-";
  const end = terminal(job) && job.finished_utc ? Date.parse(job.finished_utc) : Date.now();
  return formatDuration((Number.isFinite(end) ? end : Date.now()) / 1000 - start / 1000);
}

function checkRow(kind, name, detail) {
  const dotClass = {
    ok: "dot-ok",
    warn: "dot-warn",
    err: "dot-err",
    muted: "dot-muted",
  }[kind] || "dot-muted";
  return el("div", { class: "check-row" }, [
    el("span", { class: `dot ${dotClass}` }),
    el("strong", { text: name }),
    detail instanceof Node ? detail : el("span", { text: detail }),
  ]);
}

function boolCheck(name, value) {
  if (value === true) return checkRow("ok", name, "connected");
  if (value === false) return checkRow("err", name, "not connected");
  return checkRow("muted", name, "not reported yet");
}

function vizCheck(name, value, suffix = "") {
  if (value === true) return checkRow("ok", name, `listening${suffix}`);
  if (value === false) return checkRow("err", name, `not listening${suffix}`);
  return checkRow("muted", name, `not reported yet${suffix}`);
}

function phaseFromJob(job) {
  if (terminal(job)) return "finished";
  if (job.status === "running" || job.status === "cancelling") return "running";
  return "queued";
}

function normalizePhase(raw) {
  return PHASE_ALIASES.get(String(raw || "").toLowerCase().replace(/[^a-z0-9]/g, "")) || null;
}

function scanPhaseMarkers(text) {
  const matches = [...text.matchAll(/^PHASE:\s*(\w+)/gm)];
  if (!matches.length) return null;
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const phase = normalizePhase(matches[i][1]);
    if (phase) return phase;
  }
  return null;
}

function renderPhaseStrip(job, reportedPhase) {
  const rich = Boolean(reportedPhase);
  const phases = rich ? RICH_PHASES : COARSE_PHASES;
  const current = rich ? reportedPhase : phaseFromJob(job);
  const currentIndex = Math.max(0, phases.indexOf(current));
  const strip = el("div", { class: "phase-strip" });
  phases.forEach((phase, index) => {
    const cls = index === currentIndex ? "phase-step current" : (index < currentIndex ? "phase-step done" : "phase-step");
    strip.appendChild(el("span", { class: cls, text: phase }));
  });
  const help = rich
    ? "runner-reported phases"
    : "coarse phases; the runner does not report flight phase yet";
  return el("section", { class: "stack" }, [strip, el("p", { class: "help", text: help })]);
}

function filteredKv(pairs) {
  const div = el("div", { class: "kv" });
  for (const [k, v] of pairs) {
    if (v === null || v === undefined || v === "" || v === false || (Array.isArray(v) && !v.length)) continue;
    div.appendChild(el("div", { class: "k", text: k }));
    div.appendChild(el("div", { text: Array.isArray(v) ? v.join(" ") : String(v) }));
  }
  return div;
}

function renderOverview(job, launchText, launchError) {
  const wrap = el("details", { class: "job-overview" }, [el("summary", { text: "Overview" })]);
  wrap.appendChild(filteredKv([
    ["job_id", job.job_id],
    ["kind", job.kind],
    ["status", job.status],
    ["scenario", job.scenario],
    ["command", job.command],
    ["launch_script", job.launch_script],
    ["pid", job.pid],
    ["started_utc", job.started_utc],
    ["finished_utc", job.finished_utc],
    ["returncode", returncodeText(job)],
    ["run_dir", job.run_dir],
    ["note", job.note],
    ["interrupted_by", job.interrupted_by],
    ["orphaned_from_previous_dashboard", job.orphaned_from_previous_dashboard],
    ["hard_kill", job.hard_kill],
    ["stall_warning", job.stall_warning],
  ]));
  wrap.appendChild(el("h2", { text: "launch.sh" }));
  wrap.appendChild(el("pre", { class: "run-command", text: launchText || launchError || "launch.sh is not available yet." }));
  return wrap;
}

function renderRail(job, runStatus, cancel) {
  const viz = job.viz || {};
  const qgcIp = runStatus?.qgc_ip;
  const qgcLocalPort = runStatus?.qgc_local_port ?? 14555;
  const qgcRemotePort = runStatus?.qgc_remote_port ?? 14550;
  const rail = el("aside", { class: "rail job-rail stack" });

  rail.appendChild(el("section", { class: "tile stack" }, [
    el("div", { class: "cluster" }, [statusBadge(job.status), el("span", { class: "row-metric", text: elapsedText(job) })]),
    el("div", { class: "cluster" }, [el("span", { class: "row-meta", text: "Run dir" }), runDirLink(job)]),
    cancel,
  ]));

  const qgcRows = [
    boolCheck("MAVLink stream", runStatus?.qgc_connected),
    boolCheck("GCS heartbeat", runStatus?.qgc_gcs_heartbeat_seen),
    checkRow(qgcIp ? "ok" : "muted", "qgc_ip", qgcIp || "not reported yet"),
    checkRow("muted", "UDP ports", `${qgcLocalPort}/${qgcRemotePort}`),
  ];
  rail.appendChild(el("section", { class: "tile stack" }, [el("h2", { text: "QGroundControl" }), ...qgcRows]));

  const gzRows = [
    vizCheck("9002 enum-patch proxy", viz["9002"]),
    vizCheck("9003 raw runner bridge", viz["9003"], " (reference only)"),
    checkRow("muted", "paste URL", "ws://localhost:9002"),
    checkRow("muted", "SSH tunnel", "ssh -N -L 9002:127.0.0.1:9002 root@100.78.93.35"),
    checkRow("muted", "viewer", el("a", { href: "https://app.gazebosim.org/visualization", text: "app.gazebosim.org" })),
  ];
  const gzSection = el("section", { class: "tile stack" }, [el("h2", { text: "gz-web" }), ...gzRows]);
  if (!viz["9002"]) {
    gzSection.appendChild(el("p", { class: "help", text: "Start the proxy on the host before opening the Gazebo viewer:" }));
    gzSection.appendChild(el("pre", { class: "run-command", text: PROXY_CMD }));
  }
  rail.appendChild(gzSection);
  return rail;
}

function renderConsolePage(jobId, initialJob, launchText, launchError) {
  let job = initialJob;
  let runStatus = null;
  let nextOffset = 0;
  let eof = false;
  let finalKickSent = false;
  let truncatedNoted = false;
  let failureCount = 0;
  let logPoller = null;
  let jobPoller = null;
  let follow = true;
  let reportedPhase = null;
  const wrap = el("div", { class: "job-detail-grid" });
  const main = el("main", { class: "job-console stack" });
  const railHost = el("div", { class: "job-rail-host" });
  const phaseHost = el("div", {});
  const meta = el("div", { class: "cluster job-console-meta" });
  const errorHost = el("div", {});
  const overviewHost = el("div", {});
  const pre = el("pre", { class: "log-tail job-log-tail" });
  const followButton = el("button", { class: "btn-ghost", type: "button", text: "follow on" });
  const cancel = el("button", { class: "btn-danger", type: "button", text: "Cancel" });

  function isTerminal() {
    return TERMINAL.has(job.status);
  }

  function isNearBottom() {
    return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
  }

  function renderFollow() {
    followButton.textContent = follow ? "follow on" : "follow off";
    followButton.setAttribute("aria-pressed", follow ? "true" : "false");
  }

  function renderMeta() {
    meta.replaceChildren(
      statusBadge(job.status),
      el("span", { text: job.scenario || "" }),
      el("span", { class: "row-metric", text: elapsedText(job) }),
      runDirLink(job),
      followButton,
    );
    if (job.stall_warning) meta.appendChild(el("span", { class: "badge status-legacy", text: job.stall_warning }));
  }

  function renderAll() {
    cancel.disabled = job.status === "cancelling" || isTerminal();
    renderFollow();
    renderMeta();
    phaseHost.replaceChildren(renderPhaseStrip(job, reportedPhase));
    railHost.replaceChildren(renderRail(job, runStatus, cancel));
    const wasOpen = overviewHost.querySelector("details")?.open || false;
    const overview = renderOverview(job, launchText, launchError);
    overview.open = wasOpen;
    overviewHost.replaceChildren(overview);
  }

  function maybeStop() {
    if (!isTerminal() || !eof) return;
    logPoller?.stop();
    if (jobPoller) jobPoller.stop();
  }

  followButton.addEventListener("click", () => {
    follow = !follow;
    if (follow) pre.scrollTop = pre.scrollHeight;
    renderFollow();
  });

  pre.addEventListener("scroll", () => {
    const nextFollow = isNearBottom();
    if (nextFollow !== follow) {
      follow = nextFollow;
      renderFollow();
    }
  });

  cancel.addEventListener("click", async () => {
    if (!window.confirm(`Cancel ${job.job_id}? This SIGTERMs the runner, which tears down Gazebo and PX4 and marks the run not-accepted.`)) return;
    cancel.disabled = true;
    await postJSON(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, {});
    job.status = "cancelling";
    renderAll();
    jobPoller?.kick();
  });

  async function fetchLog() {
    try {
      const nearBottom = isNearBottom();
      const data = await getJSON(`/api/jobs/${encodeURIComponent(jobId)}/log?offset=${nextOffset}`);
      nextOffset = data.next_offset;
      eof = Boolean(data.eof);
      if (data.truncated && !truncatedNoted) {
        truncatedNoted = true;
        pre.appendChild(document.createTextNode("[log truncated]\n"));
      }
      if (data.text) {
        pre.appendChild(document.createTextNode(data.text));
        const phase = scanPhaseMarkers(data.text);
        if (phase) {
          reportedPhase = phase;
          phaseHost.replaceChildren(renderPhaseStrip(job, reportedPhase));
        }
      }
      if (follow && nearBottom) pre.scrollTop = pre.scrollHeight;
      failureCount = 0;
      errorHost.replaceChildren();
      maybeStop();
    } catch (e) {
      failureCount += 1;
      if (failureCount >= 2) errorHost.replaceChildren(el("div", { class: "error-box", text: "Log polling failed: " + ((e && e.message) || e) }));
      throw e;
    }
  }

  async function fetchJob() {
    try {
      job = await getJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
      runStatus = await readRunStatus(runIdFromDir(job.run_dir));
      renderAll();
      failureCount = 0;
      errorHost.replaceChildren();
      if (isTerminal() && !finalKickSent) {
        finalKickSent = true;
        logPoller?.kick();
      }
      maybeStop();
    } catch (e) {
      failureCount += 1;
      if (failureCount >= 2) errorHost.replaceChildren(el("div", { class: "error-box", text: "Job polling failed: " + ((e && e.message) || e) }));
      throw e;
    }
  }

  main.appendChild(phaseHost);
  main.appendChild(meta);
  main.appendChild(errorHost);
  main.appendChild(pre);
  main.appendChild(overviewHost);
  wrap.appendChild(main);
  wrap.appendChild(railHost);
  renderAll();
  logPoller = startPolling(fetchLog, { baseMs: 2000, maxMs: 15000 });
  jobPoller = startPolling(fetchJob, { baseMs: 5000, maxMs: 30000 });
  return wrap;
}

export async function renderJob(jobId) {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));

  let job;
  try {
    job = await getJSON(`/api/jobs/${encodeURIComponent(jobId)}`);
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Job not found: " + jobId }));
    return;
  }

  let launchText = "";
  let launchError = "";
  try {
    launchText = await fetchText(`/artifacts/jobs/${encodeURIComponent(job.job_id)}/launch.sh`);
  } catch (e) {
    launchError = "Failed to load launch.sh: " + ((e && e.message) || e);
  }

  app.replaceChildren(
    el("p", {}, [el("a", { href: "/jobs", text: "< back to jobs" })]),
    el("div", { class: "cluster job-title" }, [el("h1", { text: job.job_id }), statusBadge(job.status)]),
    renderConsolePage(job.job_id, job, launchText, launchError),
  );
}
