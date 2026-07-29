import { el, tabs } from "../dom.js";
import { getJSON } from "../api.js";

const BADGE = { editable: "badge-ok", readonly: "badge-info", derived: "badge-caution", dead: "badge-err", unknown: "badge-caution" };
const STATUSES = ["editable", "readonly", "derived", "dead", "unknown"];
const LEGEND = "editable = safe for a generated scenario to set; readonly = real and consumed but tuning-sensitive; derived = computed from another field; dead = present in some scenarios but never read by the runner.";

function badge(status, text = status) {
  const s = status || "unknown";
  return el("span", { class: `badge ${BADGE[s] || "badge-caution"}`, text: text || s });
}

function valueText(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function yamlText(value, indent = 0) {
  const pad = " ".repeat(indent);
  if (Array.isArray(value)) {
    if (!value.length) return "[]";
    return value.map(v => v && typeof v === "object" ? `${pad}-\n${yamlText(v, indent + 2)}` : `${pad}- ${valueText(v)}`).join("\n");
  }
  if (value && typeof value === "object") {
    return Object.entries(value).map(([k, v]) => v && typeof v === "object" ? `${pad}${k}:\n${yamlText(v, indent + 2)}` : `${pad}${k}: ${valueText(v)}`).join("\n");
  }
  return `${pad}${valueText(value)}`;
}

export async function renderScenarios() {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));
  let scenarios;
  try {
    scenarios = await getJSON("/api/scenarios");
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load scenarios: " + ((e && e.message) || e) }));
    return;
  }

  const state = { q: new URLSearchParams(location.search).get("q") || "", sortKey: "name", sortDir: 1 };
  const search = el("input", { type: "text", placeholder: "search name / run / description / vehicle...", value: state.q });
  const summary = el("p", {});
  const tableWrap = el("div", { class: "table-scroll" });

  function syncUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    history.replaceState(null, "", p.toString() ? `/scenarios?${p}` : "/scenarios");
  }

  function render() {
    const q = state.q.trim().toLowerCase();
    const rows = scenarios.filter(s => !q || ["name", "run_name", "description", "vehicle_model"].some(k => s[k] && s[k].toLowerCase().includes(q)));
    rows.sort((a, b) => {
      const av = a[state.sortKey], bv = b[state.sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av < bv ? -state.sortDir : av > bv ? state.sortDir : 0;
    });
    summary.textContent = `${rows.length} of ${scenarios.length} scenarios`;
    tableWrap.replaceChildren();
    syncUrl();
    if (!rows.length) {
      tableWrap.appendChild(el("p", { class: "help", text: scenarios.length ? "No scenarios match the current search." : "No scenarios were found." }));
      return;
    }
    const table = el("table", {});
    const head = el("tr", {});
    for (const key of ["name", "run_name", "description", "vehicle_model", "status"]) {
      const th = el("th", { class: key === "status" ? "" : "sortable", text: key + (state.sortKey === key ? (state.sortDir === 1 ? " ▲" : " ▼") : "") });
      if (key !== "status") th.addEventListener("click", () => {
        state.sortDir = state.sortKey === key ? -state.sortDir : 1;
        state.sortKey = key;
        render();
      });
      head.appendChild(th);
    }
    table.appendChild(head);
    for (const s of rows) {
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("a", { href: `/scenarios/${encodeURIComponent(s.name)}`, text: s.name })]),
        el("td", { text: s.run_name || "" }),
        el("td", { text: s.description || "" }),
        el("td", { text: s.vehicle_model || "" }),
        el("td", {}, s.error ? [badge("dead", "YAML error")] : []),
      ]));
    }
    tableWrap.appendChild(table);
  }

  search.addEventListener("input", () => {
    state.q = search.value;
    render();
  });
  app.replaceChildren(el("div", { class: "filters" }, [search]), summary, tableWrap);
  render();
}

function renderFields(fields) {
  const wrap = el("div", {}, [el("p", { class: "help", text: LEGEND })]);
  for (const status of STATUSES) {
    const rows = (fields || []).filter(f => (f.status || "unknown") === status);
    if (!rows.length) continue;
    const table = el("table", {}, [el("tr", {}, ["status", "path", "value", "note"].map(h => el("th", { text: h })))]);
    for (const f of rows) {
      table.appendChild(el("tr", {}, [el("td", {}, [badge(f.status)]), el("td", { text: f.path || "" }), el("td", { text: valueText(f.value) }), el("td", { text: f.note || "" })]));
    }
    wrap.appendChild(el("h2", { text: `${status} (${rows.length})` }));
    wrap.appendChild(el("div", { class: "table-scroll" }, [table]));
  }
  return wrap;
}

function renderLaunch(name) {
  return el("div", { class: "stack" }, [
    el("p", { class: "help", text: "Launch now has its own pre-flight page with explained inputs, host checks, and deployment blockers." }),
    el("a", { class: "btn-primary", href: `/launch?scenario=${encodeURIComponent(name)}`, text: "Open launch page" }),
  ]);
}

export async function renderScenario(name) {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));
  let scenario;
  try {
    scenario = await getJSON(`/api/scenarios/${encodeURIComponent(name)}`);
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load scenario: " + ((e && e.message) || e) }));
    return;
  }
  app.replaceChildren(
    el("p", {}, [el("a", { href: "/scenarios", text: "< back to scenarios" })]),
    el("h1", { text: scenario.name }),
    tabs([
      { label: "Fields", render: () => renderFields(scenario.fields) },
      { label: "YAML", render: () => el("pre", { text: yamlText(scenario.content) }) },
      { label: "Launch", render: () => renderLaunch(scenario.name) },
    ]),
  );
}
