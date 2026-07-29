import { el } from "../dom.js";
import { getJSON } from "../api.js";
import { mountActiveJobBanner } from "../components/active_job_banner.js";

const COLUMNS = [
  { key: "run_id", label: "run_id" },
  { key: "phase", label: "phase" },
  { key: "algorithm", label: "algorithm" },
  { key: "gnss_state", label: "gnss_state" },
  { key: "contract_status", label: "status" },
  { key: "accepted", label: "accepted" },
  { key: "horizontal_error_max_m", label: "horiz err max (m)" },
];

function sortValue(r, key) {
  return key === "horizontal_error_max_m" ? r.key_metrics.horizontal_error_max_m : r[key];
}

function distinctSorted(runs, key) {
  return [...new Set(runs.map(r => r[key]).filter(v => v))].sort();
}

function filterSelect(id, label, options, selected) {
  const select = el("select", { id, "aria-label": label });
  select.appendChild(el("option", { value: "", text: `${label}: any` }));
  for (const opt of options) {
    const o = el("option", { value: opt, text: opt });
    if (opt === selected) o.selected = true;
    select.appendChild(o);
  }
  return select;
}

function present(value) {
  return value !== null && value !== undefined && value !== "";
}

function statusKind(status) {
  if (status === "conformant") return "ok";
  if (status === "legacy" || status === "in_progress") return "warn";
  if (status === "incomplete") return "err";
  return "muted";
}

function statusNode(status) {
  const dot = {
    ok: "dot-ok",
    warn: "dot-warn",
    err: "dot-err",
    muted: "dot-muted",
  }[statusKind(status)];
  return el("span", { class: "cluster row-status" }, [
    el("span", { class: `dot ${dot}` }),
    el("span", { text: status || "unknown" }),
  ]);
}

function runMeta(r) {
  return [r.phase, r.scenario_name, r.algorithm, r.gnss_state]
    .filter(present)
    .map((value) => el("span", { text: value }));
}

function runMetrics(r) {
  const nodes = [];
  if (r.accepted !== null && r.accepted !== undefined) {
    nodes.push(el("span", { class: "row-metric", text: `accepted ${String(r.accepted)}` }));
  }
  const horizontalError = r.key_metrics.horizontal_error_max_m;
  if (horizontalError !== null && horizontalError !== undefined) {
    nodes.push(el("span", { class: "row-metric", text: `${horizontalError.toFixed(3)} m` }));
  }
  return nodes;
}

function runRow(r) {
  const left = [el("div", { class: "row-main", text: r.run_id })];
  const meta = runMeta(r);
  if (meta.length) left.push(el("div", { class: "row-meta" }, meta));
  const right = [statusNode(r.contract_status), ...runMetrics(r), el("span", { class: "row-chevron", text: ">" })];
  return el("a", { class: "list-row archive-row", href: `/runs/${encodeURIComponent(r.run_id)}` }, [
    el("div", {}, left),
    el("div", { class: "cluster archive-row-tail" }, right),
  ]);
}

export async function renderList() {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(el("span", { class: "spinner" }));
  app.appendChild(document.createTextNode("loading..."));

  let runs;
  try {
    runs = await getJSON("/api/runs");
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("div", { class: "error-box", text: "Failed to load runs: " + ((e && e.message) || e) }));
    return;
  }

  const params = new URLSearchParams(location.search);
  const state = {
    algorithm: params.get("algorithm") || "",
    gnss_state: params.get("gnss_state") || "",
    status: params.get("status") || "",
    q: params.get("q") || "",
    sortKey: "run_id",
    sortDir: 1,
  };

  app.innerHTML = "";

  const searchInput = el("input", { type: "text", "aria-label": "search runs", placeholder: "search run_id / scenario_name...", value: state.q });
  const algoSelect = filterSelect("f-algo", "algorithm", distinctSorted(runs, "algorithm"), state.algorithm);
  const gnssSelect = filterSelect("f-gnss", "gnss_state", distinctSorted(runs, "gnss_state"), state.gnss_state);
  const statusSelect = filterSelect("f-status", "status", distinctSorted(runs, "contract_status"), state.status);

  const summary = el("p", {});
  const sortHost = el("div", { class: "cluster sort-controls" });
  const listHost = el("div", { class: "list" });

  function syncUrl() {
    const p = new URLSearchParams();
    if (state.algorithm) p.set("algorithm", state.algorithm);
    if (state.gnss_state) p.set("gnss_state", state.gnss_state);
    if (state.status) p.set("status", state.status);
    if (state.q) p.set("q", state.q);
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  function render() {
    const q = state.q.trim().toLowerCase();
    const filtered = runs.filter(r =>
      (!state.algorithm || r.algorithm === state.algorithm) &&
      (!state.gnss_state || r.gnss_state === state.gnss_state) &&
      (!state.status || r.contract_status === state.status) &&
      (!q ||
        (r.run_id && r.run_id.toLowerCase().includes(q)) ||
        (r.scenario_name && r.scenario_name.toLowerCase().includes(q)))
    );

    filtered.sort((a, b) => {
      const av = sortValue(a, state.sortKey);
      const bv = sortValue(b, state.sortKey);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -state.sortDir;
      if (av > bv) return state.sortDir;
      return 0;
    });

    summary.textContent =
      `${filtered.length} of ${runs.length} runs` +
      (state.algorithm || state.gnss_state || state.status || q
        ? ` (algorithm=${state.algorithm || "any"}, gnss_state=${state.gnss_state || "any"}, ` +
          `status=${state.status || "any"}${q ? `, search="${q}"` : ""})`
        : "");

    sortHost.replaceChildren(el("span", { class: "help", text: "Sort" }));
    for (const col of COLUMNS) {
      const active = state.sortKey === col.key;
      const arrow = active ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
      const button = el("button", {
        class: active ? "btn sort-active" : "btn-ghost",
        type: "button",
        text: col.label + arrow,
        "aria-pressed": active ? "true" : "false",
      });
      button.addEventListener("click", () => {
        if (state.sortKey === col.key) state.sortDir = -state.sortDir;
        else {
          state.sortKey = col.key;
          state.sortDir = 1;
        }
        render();
      });
      sortHost.appendChild(button);
    }

    listHost.replaceChildren();
    for (const r of filtered.slice(0, 500)) {
      listHost.appendChild(runRow(r));
    }
    if (!filtered.length) {
      listHost.appendChild(el("p", { class: "empty", text: runs.length ? "No runs match the current filters." : "No runs have been indexed yet." }));
    }
    syncUrl();
  }

  searchInput.addEventListener("input", () => {
    state.q = searchInput.value;
    render();
  });
  algoSelect.addEventListener("change", () => {
    state.algorithm = algoSelect.value;
    render();
  });
  gnssSelect.addEventListener("change", () => {
    state.gnss_state = gnssSelect.value;
    render();
  });
  statusSelect.addEventListener("change", () => {
    state.status = statusSelect.value;
    render();
  });

  const bannerHost = el("div", {});
  app.appendChild(bannerHost);
  mountActiveJobBanner(bannerHost);
  app.appendChild(el("div", { class: "filters" }, [searchInput, algoSelect, gnssSelect, statusSelect]));
  app.appendChild(sortHost);
  app.appendChild(summary);
  app.appendChild(listHost);
  render();
}
