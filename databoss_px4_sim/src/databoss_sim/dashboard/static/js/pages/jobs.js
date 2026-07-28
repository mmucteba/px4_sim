import { getJSON } from "../api.js";
import { el } from "../dom.js";
import { mountActiveJobBanner } from "../components/active_job_banner.js";
import { startPolling } from "../poller.js";

function statusBadge(status) {
  return el("span", { class: `badge status-${status || "unknown"}`, text: status || "unknown" });
}

function formatDate(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}

function duration(job) {
  const start = Date.parse(job.started_utc || "");
  if (!Number.isFinite(start)) return "";
  const end = Number.isFinite(Date.parse(job.finished_utc || "")) ? Date.parse(job.finished_utc) : Date.now();
  const seconds = Math.max(0, Math.floor((end - start) / 1000));
  const mins = Math.floor(seconds / 60);
  const hrs = Math.floor(mins / 60);
  return hrs ? `${hrs}h ${mins % 60}m` : `${mins}m ${seconds % 60}s`;
}

function runDirLink(job) {
  if (!job.run_dir) return "";
  const runId = String(job.run_dir).split("/").filter(Boolean).pop();
  return el("a", { href: `/artifacts/runs/${encodeURIComponent(runId)}/`, text: runId || job.run_dir });
}

export async function renderJobs() {
  const app = document.getElementById("app");
  const bannerHost = el("div", {});
  const searchInput = el("input", { type: "text", placeholder: "search job_id / scenario...", value: new URLSearchParams(location.search).get("q") || "" });
  const summary = el("p", {});
  const tableWrap = el("div", { class: "table-scroll" });
  const errorHost = el("div", {});
  const state = { jobs: [], q: searchInput.getAttribute("value") || "" };

  app.replaceChildren(bannerHost, el("div", { class: "filters" }, [searchInput]), errorHost, summary, tableWrap);
  mountActiveJobBanner(bannerHost);

  function syncUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    history.replaceState(null, "", p.toString() ? `/jobs?${p}` : "/jobs");
  }

  function renderTable() {
    const q = state.q.trim().toLowerCase();
    const jobs = state.jobs.filter((job) =>
      !q ||
      (job.job_id && job.job_id.toLowerCase().includes(q)) ||
      (job.scenario && job.scenario.toLowerCase().includes(q))
    );
    summary.textContent = `${jobs.length} of ${state.jobs.length} jobs`;
    tableWrap.replaceChildren();
    syncUrl();
    if (!jobs.length) {
      tableWrap.appendChild(el("p", { class: "help", text: state.jobs.length ? "No jobs match the current search." : "No dashboard jobs have been recorded yet." }));
      return;
    }

    const table = el("table", {});
    table.appendChild(el("tr", {}, ["job_id", "kind", "status", "scenario", "started", "duration", "run_dir"].map((h) => el("th", { text: h }))));
    for (const job of jobs) {
      const statusTd = el("td", {}, [statusBadge(job.status)]);
      const runDir = runDirLink(job);
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("a", { href: `/jobs/${encodeURIComponent(job.job_id)}`, text: job.job_id })]),
        el("td", { text: job.kind || "" }),
        statusTd,
        el("td", { text: job.scenario || "" }),
        el("td", { text: formatDate(job.started_utc) }),
        el("td", { text: duration(job) }),
        el("td", {}, runDir ? [runDir] : []),
      ]));
    }
    tableWrap.appendChild(table);
  }

  async function loadJobs() {
    try {
      const data = await getJSON("/api/jobs");
      state.jobs = data.jobs || [];
      errorHost.replaceChildren();
      renderTable();
    } catch (e) {
      errorHost.replaceChildren(el("div", { class: "error-box", text: "Failed to load jobs: " + ((e && e.message) || e) }));
      throw e;
    }
  }

  searchInput.addEventListener("input", () => {
    state.q = searchInput.value;
    renderTable();
  });

  startPolling(loadJobs, { baseMs: 10000, maxMs: 30000 });
}
