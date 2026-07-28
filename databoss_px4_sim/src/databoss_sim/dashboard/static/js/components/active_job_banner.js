import { getJSON, postJSON } from "../api.js";
import { el } from "../dom.js";
import { startPolling } from "../poller.js";

function statusBadge(job) {
  return el("span", { class: `badge status-${job.status || "unknown"}`, text: job.status || "unknown" });
}

function elapsedText(startedUtc) {
  const start = Date.parse(startedUtc || "");
  if (!Number.isFinite(start)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - start) / 1000));
  const mins = Math.floor(seconds / 60);
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  const remSecs = seconds % 60;
  return hrs ? `${hrs}h ${remMins}m ${remSecs}s` : `${remMins}m ${remSecs}s`;
}

function runDirLink(job) {
  if (!job.run_dir) return el("span", { class: "help", text: "run_dir pending" });
  const runId = String(job.run_dir).split("/").filter(Boolean).pop();
  return el("a", { href: `/artifacts/runs/${encodeURIComponent(runId)}/`, text: runId || job.run_dir });
}

export async function mountActiveJobBanner(container) {
  let active = null;
  let elapsedNode = null;
  let tickTimer = null;
  let lastFetch = 0;
  let forceNext = true;

  function scheduleTick() {
    clearTimeout(tickTimer);
    if (!active || !elapsedNode) return;
    elapsedNode.textContent = elapsedText(active.started_utc);
    tickTimer = setTimeout(scheduleTick, 1000);
  }

  function render() {
    container.replaceChildren();
    elapsedNode = null;
    if (!active) {
      clearTimeout(tickTimer);
      return;
    }

    elapsedNode = el("span", { text: elapsedText(active.started_utc) });
    const cancel = el("button", { type: "button", text: "Cancel" });
    cancel.disabled = active.status === "cancelling";
    cancel.addEventListener("click", async () => {
      if (!window.confirm(`Cancel ${active.job_id}? This SIGTERMs the runner, which tears down Gazebo and PX4 and marks the run not-accepted.`)) return;
      cancel.disabled = true;
      await postJSON(`/api/jobs/${encodeURIComponent(active.job_id)}/cancel`, {});
      forceNext = true;
      poller.kick();
    });

    const bits = [
      el("strong", {}, [el("a", { href: `/jobs/${encodeURIComponent(active.job_id)}`, text: active.job_id })]),
      statusBadge(active),
      el("span", {}, [document.createTextNode("elapsed "), elapsedNode]),
      el("span", { text: active.scenario || "" }),
      runDirLink(active),
    ];
    if (active.stall_warning) {
      bits.push(el("span", { class: "badge status-legacy", text: active.stall_warning }));
    }
    bits.push(cancel);
    container.appendChild(el("div", { class: "banner" }, bits));
    scheduleTick();
  }

  async function refresh() {
    const now = Date.now();
    const minMs = active ? 5000 : 20000;
    if (!forceNext && now - lastFetch < minMs) return;
    forceNext = false;
    lastFetch = now;
    const data = await getJSON("/api/jobs");
    active = data.active || null;
    render();
  }

  function onVisible() {
    if (document.visibilityState === "visible") {
      forceNext = true;
      poller.kick();
    }
  }

  const poller = startPolling(refresh, { baseMs: 5000, maxMs: 30000 });
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("beforeunload", () => {
    clearTimeout(tickTimer);
    document.removeEventListener("visibilitychange", onVisible);
  });
}
