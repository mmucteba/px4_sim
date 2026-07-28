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
  const select = el("select", { id });
  select.appendChild(el("option", { value: "", text: `${label}: any` }));
  for (const opt of options) {
    const o = el("option", { value: opt, text: opt });
    if (opt === selected) o.selected = true;
    select.appendChild(o);
  }
  return select;
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

  const searchInput = el("input", { type: "text", placeholder: "search run_id / scenario_name...", value: state.q });
  const algoSelect = filterSelect("f-algo", "algorithm", distinctSorted(runs, "algorithm"), state.algorithm);
  const gnssSelect = filterSelect("f-gnss", "gnss_state", distinctSorted(runs, "gnss_state"), state.gnss_state);
  const statusSelect = filterSelect("f-status", "status", distinctSorted(runs, "contract_status"), state.status);

  const summary = el("p", {});
  const tableWrap = el("div", { class: "table-scroll" });

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

    const table = el("table", {});
    const headRow = el("tr", {});
    for (const col of COLUMNS) {
      const arrow = state.sortKey === col.key ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
      const th = el("th", { class: "sortable", text: col.label + arrow });
      th.addEventListener("click", () => {
        if (state.sortKey === col.key) state.sortDir = -state.sortDir;
        else {
          state.sortKey = col.key;
          state.sortDir = 1;
        }
        render();
      });
      headRow.appendChild(th);
    }
    table.appendChild(headRow);

    for (const r of filtered.slice(0, 500)) {
      const statusTd = el("td", {});
      statusTd.appendChild(el("span", { class: `badge status-${r.contract_status}`, text: r.contract_status }));
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("a", { href: `/runs/${r.run_id}`, text: r.run_id })]),
        el("td", { text: r.phase || "" }),
        el("td", { text: r.algorithm || "" }),
        el("td", { text: r.gnss_state || "" }),
        statusTd,
        el("td", { text: r.accepted === null ? "" : String(r.accepted) }),
        el("td", { text: r.key_metrics.horizontal_error_max_m != null ? r.key_metrics.horizontal_error_max_m.toFixed(3) : "" }),
      ]));
    }

    tableWrap.innerHTML = "";
    tableWrap.appendChild(table);
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
  app.appendChild(summary);
  app.appendChild(tableWrap);
  render();
}
