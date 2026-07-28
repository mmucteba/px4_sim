import { getJSON, postJSON } from "../api.js";
import { el, kv, tabs } from "../dom.js";
import { startPolling } from "../poller.js";

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "crashed"]);
const PROXY_CMD = "venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py --listen-port 9002 --upstream ws://127.0.0.1:9003";

function statusBadge(status) {
  return el("span", { class: `badge status-${status || "unknown"}`, text: status || "unknown" });
}

function runIdFromDir(runDir) {
  return runDir ? String(runDir).split("/").filter(Boolean).pop() : "";
}

function runDirLink(job) {
  const runId = runIdFromDir(job.run_dir);
  return runId ? el("a", { href: `/artifacts/runs/${encodeURIComponent(runId)}/`, text: runId }) : document.createTextNode("-");
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

function listeningBadge(on) {
  return el("span", { class: `badge ${on ? "badge-ok" : "badge-caution"}`, text: on ? "listening" : "not listening" });
}

function renderLinks(job, status) {
  const viz = job.viz || {};
  const qgcIp = status?.qgc_ip || "100.109.200.5";
  const wrap = el("div", {});
  wrap.appendChild(el("h2", { text: "QGroundControl" }));
  wrap.appendChild(kv([
    ["qgc_ip", qgcIp],
    ["local UDP port", 14555],
    ["remote UDP port", 14550],
    ["QGC target", "UDP MAVLink target, not a URL"],
  ]));
  if (status && ("qgc_connected" in status || "qgc_gcs_heartbeat_seen" in status)) {
    wrap.appendChild(kv([
      ["qgc_connected", status.qgc_connected],
      ["qgc_gcs_heartbeat_seen", status.qgc_gcs_heartbeat_seen],
    ]));
  }

  wrap.appendChild(el("h2", { text: "gz-web" }));
  wrap.appendChild(kv([
    ["9002 enum-patch proxy", viz["9002"] === undefined ? "unknown" : (viz["9002"] ? "listening" : "not listening")],
    ["9003 raw runner bridge", viz["9003"] === undefined ? "unknown" : (viz["9003"] ? "listening (reference only)" : "not listening (reference only)")],
  ]));
  wrap.appendChild(el("p", {}, [document.createTextNode("Browser entry point: "), el("a", { href: "https://app.gazebosim.org/visualization", text: "Gazebo visualization" })]));
  wrap.appendChild(kv([
    ["paste URL", "ws://localhost:9002"],
    ["SSH tunnel", "ssh -N -L 9002:127.0.0.1:9002 root@100.78.93.35"],
  ]));
  wrap.appendChild(el("p", {}, [document.createTextNode("9002 "), listeningBadge(Boolean(viz["9002"])), document.createTextNode(" 9003 "), listeningBadge(Boolean(viz["9003"]))]));
  if (!viz["9002"]) {
    wrap.appendChild(el("p", { class: "help", text: "Start the proxy on the host before opening the Gazebo viewer:" }));
    wrap.appendChild(el("pre", { class: "run-command", text: PROXY_CMD }));
  }
  return wrap;
}

function renderOverview(job, launchText, launchError) {
  const wrap = el("div", {});
  wrap.appendChild(kv([
    ["job_id", job.job_id],
    ["kind", job.kind],
    ["status", job.status],
    ["scenario", job.scenario],
    ["command", (job.command || []).join(" ")],
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

function renderConsole(jobId, initialJob) {
  let job = initialJob;
  let nextOffset = 0;
  let eof = false;
  let finalKickSent = false;
  let truncatedNoted = false;
  let failureCount = 0;
  let logPoller = null;
  let jobPoller = null;
  const wrap = el("div", {});
  const meta = el("div", { class: "job-actions" });
  const errorHost = el("div", {});
  const pre = el("pre", { class: "log-tail" });
  wrap.appendChild(meta);
  wrap.appendChild(errorHost);
  wrap.appendChild(pre);

  function terminal() {
    return TERMINAL.has(job.status);
  }

  function maybeStop() {
    if (!terminal() || !eof) return;
    logPoller?.stop();
    jobPoller?.stop();
  }

  function renderMeta() {
    meta.replaceChildren(statusBadge(job.status), el("span", { text: job.scenario || "" }), runDirLink(job));
    if (job.stall_warning) meta.appendChild(el("span", { class: "badge status-legacy", text: job.stall_warning }));
  }

  async function fetchLog() {
    try {
      const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
      const data = await getJSON(`/api/jobs/${encodeURIComponent(jobId)}/log?offset=${nextOffset}`);
      nextOffset = data.next_offset;
      eof = Boolean(data.eof);
      if (data.truncated && !truncatedNoted) {
        truncatedNoted = true;
        pre.appendChild(document.createTextNode("[log truncated]\n"));
      }
      if (data.text) pre.appendChild(document.createTextNode(data.text));
      if (nearBottom) pre.scrollTop = pre.scrollHeight;
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
      renderMeta();
      failureCount = 0;
      errorHost.replaceChildren();
      if (terminal() && !finalKickSent) {
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

  renderMeta();
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

  const cancel = el("button", { type: "button", text: "Cancel" });
  cancel.disabled = job.status === "cancelling" || TERMINAL.has(job.status);
  cancel.addEventListener("click", async () => {
    if (!window.confirm(`Cancel ${job.job_id}? This SIGTERMs the runner, which tears down Gazebo and PX4 and marks the run not-accepted.`)) return;
    cancel.disabled = true;
    await postJSON(`/api/jobs/${encodeURIComponent(job.job_id)}/cancel`, {});
    location.reload();
  });

  app.replaceChildren(
    el("p", {}, [el("a", { href: "/jobs", text: "< back to jobs" })]),
    el("div", { class: "job-actions" }, [el("h1", { text: job.job_id }), statusBadge(job.status), cancel])
  );

  app.appendChild(tabs([
    { label: "Console", render: () => renderConsole(job.job_id, job) },
    {
      label: "Overview",
      render: async () => {
        let text = "";
        let err = "";
        try {
          text = await fetchText(`/artifacts/jobs/${encodeURIComponent(job.job_id)}/launch.sh`);
        } catch (e) {
          err = "Failed to load launch.sh: " + ((e && e.message) || e);
        }
        const fresh = await getJSON(`/api/jobs/${encodeURIComponent(job.job_id)}`);
        return renderOverview(fresh, text, err);
      },
    },
    {
      label: "Links",
      render: async () => {
        const fresh = await getJSON(`/api/jobs/${encodeURIComponent(job.job_id)}`);
        const status = await readRunStatus(runIdFromDir(fresh.run_dir));
        return renderLinks(fresh, status);
      },
    },
  ]));
}
