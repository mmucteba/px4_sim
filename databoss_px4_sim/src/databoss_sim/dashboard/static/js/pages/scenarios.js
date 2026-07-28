import { el, tabs } from "../dom.js";
import { getJSON, postJSON } from "../api.js";

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

function numberInput(name, value, extra = {}) {
  return el("input", { name, type: "number", step: "0.1", value, ...extra });
}

function addParsed(body, form, name, parse = (v) => v) {
  const value = form.elements[name].value.trim();
  if (value !== "") body[name] = parse(value);
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
  const form = el("form", { class: "gen" });
  const result = el("div", {});
  const inputs = {
    hover_s: numberInput("hover_s", "25"),
    startup_timeout_s: numberInput("startup_timeout_s", "150"),
    world_ready_timeout_s: numberInput("world_ready_timeout_s", "120"),
    land_timeout_s: numberInput("land_timeout_s", "70"),
    gnss_start_used: numberInput("gnss_start_used", "10", { step: "1" }),
    gnss_loss_after_takeoff_s: numberInput("gnss_loss_after_takeoff_s", "", { placeholder: "use scenario" }),
    post_loss_hover_s: numberInput("post_loss_hover_s", ""),
    failsafe_profile: el("select", { name: "failsafe_profile" }, ["", "default_px4", "delayed_observation"].map(v => el("option", { value: v, text: v || "blank" }))),
    global_position_timeout_s: numberInput("global_position_timeout_s", "90"),
    global_position_stable_s: numberInput("global_position_stable_s", "5"),
    no_global_position_gate: el("input", { name: "no_global_position_gate", type: "checkbox" }),
    qgc_ip: el("input", { name: "qgc_ip", type: "text", value: "100.109.200.5" }),
    note: el("input", { name: "note", type: "text" }),
  };
  form.appendChild(el("p", { class: "help", text: "QGC and gz-web stay enabled for scenario launches." }));
  for (const [label, input] of Object.entries(inputs)) {
    form.appendChild(el("label", { text: label }));
    form.appendChild(input);
  }
  form.appendChild(el("button", { type: "submit", text: "Launch" }));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    result.replaceChildren();
    const body = { scenario: name, no_global_position_gate: inputs.no_global_position_gate.checked };
    for (const key of ["hover_s", "startup_timeout_s", "world_ready_timeout_s", "land_timeout_s", "gnss_loss_after_takeoff_s", "post_loss_hover_s", "global_position_timeout_s", "global_position_stable_s"]) addParsed(body, form, key, parseFloat);
    addParsed(body, form, "gnss_start_used", v => parseInt(v, 10));
    for (const key of ["failsafe_profile", "qgc_ip", "note"]) addParsed(body, form, key);
    try {
      const data = await postJSON("/api/launch", body);
      location.href = `/jobs/${encodeURIComponent(data.job_id)}`;
    } catch (e) {
      const active = e.detail && e.detail.active_job_id;
      const text = e.status === 401 ? "Write token required. Set the write token on the Create page." :
        e.status === 409 ? `Launch blocked by active job: ${active || e.message}` :
        "Launch failed: " + ((e && e.message) || e);
      result.replaceChildren(el("div", { class: "error-box", text }));
    }
  });
  return el("div", {}, [form, result]);
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
