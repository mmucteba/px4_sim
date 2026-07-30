import { getJSON, postJSON } from "../api.js";
import { el } from "../dom.js";

const DEFAULTS = {
  hover_s: "25",
  startup_timeout_s: "150",
  world_ready_timeout_s: "120",
  land_timeout_s: "70",
  gnss_start_used: "10",
  gnss_loss_after_takeoff_s: "",
  post_loss_hover_s: "",
  failsafe_profile: "",
  global_position_timeout_s: "90",
  global_position_stable_s: "5",
  no_global_position_gate: false,
  qgc_ip: "",
  ignore_memory_guard: false,
  note: "",
};

const HOVER_HELP = "Total airborne budget. The runner subtracts takeoff climb, warmup, and for GNSS-loss runs the pre-cut wait from this value. This launch field is authoritative; the scenario's route.duration_s is never read by the runner.";
const POST_LOSS_HOVER_HELP = "When set, directly sets the post-GNSS-cut commanded hold and replaces the hover_s subtraction. For GNSS-denied work, this is the hold number that actually matters; the scenario's route.duration_s is never read by the runner.";
const RUNNER_GNSS_LOSS_AFTER_OFFBOARD_DEFAULT_S = 3.0;

const FIELD_GROUPS = [
  {
    legend: "Flight",
    fields: [
      ["hover_s", "number", HOVER_HELP],
      ["gnss_start_used", "number", "Satellite count PX4 starts with. 10 = healthy GNSS."],
    ],
  },
  {
    legend: "GNSS loss",
    fields: [
      ["gnss_loss_after_takeoff_s", "number", "Seconds after takeoff to cut GNSS. Leave blank for no cut."],
      ["post_loss_hover_s", "number", POST_LOSS_HOVER_HELP],
      ["failsafe_profile", "select", "How PX4 reacts to losing position. default_px4 fails safe fast; delayed_observation waits so you can observe the drift."],
    ],
  },
  {
    legend: "Timeouts",
    fields: [
      ["startup_timeout_s", "number", "Give up if PX4 has not come up in this many seconds."],
      ["world_ready_timeout_s", "number", "Give up if Gazebo has not loaded the world in this many seconds."],
      ["land_timeout_s", "number", "Give up if the landing has not finished in this many seconds."],
      ["global_position_timeout_s", "number", "Give up waiting for a valid global position after this many seconds."],
      ["global_position_stable_s", "number", "Global position must stay valid this long before takeoff is allowed."],
    ],
  },
  {
    legend: "Advanced",
    fields: [
      ["no_global_position_gate", "checkbox", "Take off without waiting for a valid global position. Only for deliberate GNSS-denied starts."],
      ["qgc_ip", "text", "Where QGroundControl is running. MAVLink is sent to this address."],
      ["ignore_memory_guard", "checkbox", "Launch even when free memory is below the guard. Risks the run being OOM-killed."],
      ["note", "text", "Free text stored with the job, for your own reference."],
    ],
  },
];

function numberInput(name) {
  const attrs = { name, type: "number", step: name === "gnss_start_used" ? "1" : "0.1", value: DEFAULTS[name] };
  if (name === "gnss_loss_after_takeoff_s") attrs.placeholder = "no cut";
  if (name === "post_loss_hover_s") attrs.placeholder = "use hover_s";
  return el("input", attrs);
}

function addParsed(body, form, name, parse = (v) => v) {
  const value = form.elements[name].value.trim();
  if (value !== "") body[name] = parse(value);
}

function scenarioMeta(scenario) {
  const facts = [];
  if (scenario.vehicle_model) facts.push(`vehicle ${scenario.vehicle_model}`);
  if (scenario.world) facts.push(`world ${scenario.world}`);
  if (scenario.run_name) facts.push(`run ${scenario.run_name}`);
  if (scenario.description) facts.push(scenario.description);
  return facts;
}

function matchesSearch(scenario, q) {
  if (!q) return true;
  return ["name", "run_name", "description", "vehicle_model", "world"].some((key) => {
    const value = scenario[key];
    return value && String(value).toLowerCase().includes(q);
  });
}

function dotClass(kind) {
  return {
    ok: "dot-ok",
    warn: "dot-warn",
    err: "dot-err",
    muted: "dot-muted",
  }[kind] || "dot-muted";
}

function checkRow(kind, name, detail) {
  return el("div", { class: "check-row" }, [
    el("span", { class: `dot ${dotClass(kind)}` }),
    el("strong", { text: name }),
    el("span", { text: detail }),
  ]);
}

function hasOwn(obj, key) {
  return obj && Object.prototype.hasOwnProperty.call(obj, key);
}

function finiteScenarioNumber(obj, key) {
  if (!hasOwn(obj, key)) return { ok: false, missing: true };
  const value = Number(obj[key]);
  return Number.isFinite(value) ? { ok: true, value } : { ok: false, missing: false };
}

function finiteInput(input) {
  const raw = input.value.trim();
  if (raw === "") return { ok: false, blank: true };
  const value = Number(raw);
  return Number.isFinite(value) ? { ok: true, value } : { ok: false, blank: false };
}

function fmtSeconds(value) {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/, "");
}

function selectedScenarioContent(state) {
  return state.selectedDetail?.content || null;
}

function gnssLossConfigured(content, inputs) {
  const launchOverride = finiteInput(inputs.gnss_loss_after_takeoff_s);
  if (launchOverride.ok) return true;
  return Boolean(content?.gnss?.loss_enabled);
}

function renderEffectiveHoldPreview(state, inputs) {
  const panel = el("section", { class: "tile stack" }, [el("h2", { text: "Effective Hold" })]);
  panel.appendChild(checkRow("muted", "Authoritative field", "route.duration_s in the scenario is ignored; these launch inputs control the runner."));

  if (state.selectedDetailLoading) {
    panel.appendChild(checkRow("muted", "Scenario timing", "loading selected scenario..."));
    return panel;
  }
  if (state.selectedDetailError) {
    panel.appendChild(checkRow("err", "Scenario timing", state.selectedDetailError));
    return panel;
  }

  const content = selectedScenarioContent(state);
  if (!content) {
    panel.appendChild(checkRow("warn", "Scenario timing", "unavailable until the selected scenario detail is loaded."));
    return panel;
  }

  const control = content.control || {};
  if (!hasOwn(content, "control") || !hasOwn(control, "mode")) {
    panel.appendChild(checkRow("warn", "Scenario timing", "unavailable: selected scenario does not declare control.mode."));
    return panel;
  }
  if (control.mode !== "offboard_local_position_hold") {
    panel.appendChild(checkRow("warn", "Scenario timing", `unavailable: control.mode is ${control.mode}, not offboard_local_position_hold.`));
    return panel;
  }

  const hover = finiteInput(inputs.hover_s);
  if (!hover.ok) {
    panel.appendChild(checkRow("warn", "hover_s", "unavailable: hover_s must be a number."));
    return panel;
  }

  const takeoff = finiteScenarioNumber(control, "start_after_takeoff_s");
  const warmup = finiteScenarioNumber(control, "warmup_s");
  if (!takeoff.ok || !warmup.ok) {
    const missing = [];
    if (!takeoff.ok) missing.push("control.start_after_takeoff_s");
    if (!warmup.ok) missing.push("control.warmup_s");
    panel.appendChild(checkRow("warn", "Scenario timing", `unavailable: ${missing.join(" and ")} must be present numeric values in the selected scenario.`));
    return panel;
  }

  const gnssLoss = gnssLossConfigured(content, inputs);
  const postLoss = finiteInput(inputs.post_loss_hover_s);
  const waitParts = [
    `takeoff ${fmtSeconds(takeoff.value)}`,
    `warmup ${fmtSeconds(warmup.value)}`,
  ];
  let preWait = takeoff.value + warmup.value;
  let preCutSource = "";
  if (gnssLoss) {
    const preCut = finiteScenarioNumber(control, "gnss_loss_after_offboard_s");
    const preCutValue = preCut.ok ? preCut.value : RUNNER_GNSS_LOSS_AFTER_OFFBOARD_DEFAULT_S;
    preCutSource = preCut.ok ? "" : " (runner default; scenario omits control.gnss_loss_after_offboard_s)";
    preWait += preCutValue;
    waitParts.push(`pre-cut ${fmtSeconds(preCutValue)}${preCutSource}`);
  }

  const preWaitText = `${fmtSeconds(preWait)} (${waitParts.join(" + ")})`;
  if (gnssLoss && postLoss.ok) {
    const clamped = Math.max(0, postLoss.value);
    panel.appendChild(checkRow(
      clamped > 0 ? "ok" : "warn",
      "Commanded hold",
      `post_loss_hover_s ${fmtSeconds(postLoss.value)} replaces hover_s subtraction = ${fmtSeconds(clamped)} s post-GNSS-cut commanded hold; pre-waits still occur before the cut: ${preWaitText}.`,
    ));
    if (postLoss.value <= 0) {
      panel.appendChild(checkRow("warn", "Clamp warning", `runner clamps max(0, ${fmtSeconds(postLoss.value)}) to ${fmtSeconds(clamped)} s, so this holds for no time after GNSS cut.`));
    }
    return panel;
  }

  const rawHold = hover.value - preWait;
  const clampedHold = Math.max(0, rawHold);
  panel.appendChild(checkRow(
    clampedHold > 0 ? "ok" : "warn",
    "Commanded hold",
    `hover_s ${fmtSeconds(hover.value)} - pre-waits ${preWaitText} = ${fmtSeconds(rawHold)} s, runner commands ${fmtSeconds(clampedHold)} s hold.`,
  ));
  if (rawHold <= 0) {
    panel.appendChild(checkRow("warn", "Clamp warning", `runner clamps max(0, ${fmtSeconds(rawHold)}) to 0 s; this launch would hold for no time before landing.`));
  }
  return panel;
}

function fieldNode(name, type, help) {
  let input;
  if (type === "number") {
    input = numberInput(name);
  } else if (type === "select") {
    input = el("select", { name }, [
      el("option", { value: "", text: "use the scenario's own setting" }),
      el("option", { value: "default_px4", text: "default_px4" }),
      el("option", { value: "delayed_observation", text: "delayed_observation" }),
    ]);
  } else if (type === "checkbox") {
    input = el("input", { name, type: "checkbox" });
    input.checked = DEFAULTS[name];
  } else {
    input = el("input", { name, type: "text", value: DEFAULTS[name] });
  }
  return { input, node: el("div", { class: "field" }, [el("label", { for: name, text: name }), input, el("p", { class: "help", text: help })]) };
}

function statusFromSummary(summary) {
  if ((summary?.fail || 0) > 0) return "err";
  if ((summary?.warn || 0) > 0) return "warn";
  return "ok";
}

function renderScenarioList(state, onSelect) {
  const q = state.query.trim().toLowerCase();
  const rows = state.scenarios.filter((scenario) => matchesSearch(scenario, q));
  rows.sort((a, b) => (a.name || "").localeCompare(b.name || ""));

  const list = el("div", { class: "list" });
  if (!rows.length) {
    return el("p", { class: "empty", text: state.scenarios.length ? "No scenarios match the current search." : "No scenarios were found." });
  }
  for (const scenario of rows) {
    const meta = scenarioMeta(scenario).map((fact) => el("span", { text: fact }));
    const children = [el("div", { class: "row-main", text: scenario.name })];
    if (meta.length) children.push(el("div", { class: "row-meta" }, meta));
    const row = el("a", {
      class: `list-row${state.selected?.name === scenario.name ? " selected" : ""}`,
      href: `/launch?scenario=${encodeURIComponent(scenario.name)}`,
    }, [el("div", {}, children)]);
    row.addEventListener("click", (ev) => {
      ev.preventDefault();
      onSelect(scenario);
    });
    list.appendChild(row);
  }
  return list;
}

function renderChecks(state, inputs, refreshChecks) {
  const panel = el("section", { class: "tile stack" });
  panel.appendChild(el("div", { class: "cluster" }, [
    el("h2", { text: "Pre-flight" }),
    el("button", { class: "btn-ghost", type: "button", text: "Re-check" }),
  ]));
  const button = panel.querySelector("button");
  button.addEventListener("click", refreshChecks);

  if (state.checksLoading) {
    panel.appendChild(checkRow("muted", "Checks", "running pre-flight checks..."));
    return panel;
  }
  if (state.checkError) {
    panel.appendChild(checkRow("err", "Checks", state.checkError));
  }

  const host = state.host;
  if (host) {
    if (host.mem_ok === null) {
      panel.appendChild(checkRow("warn", "Memory", `MemAvailable unreadable; guard is ${host.mem_guard_mb} MB.`));
    } else if (host.mem_ok) {
      panel.appendChild(checkRow("ok", "Memory", `${host.mem_available_mb} MB available; guard is ${host.mem_guard_mb} MB.`));
    } else if (inputs.ignore_memory_guard.checked) {
      panel.appendChild(checkRow("warn", "Memory", `${host.mem_available_mb} MB available is below the ${host.mem_guard_mb} MB guard; override is enabled.`));
    } else {
      panel.appendChild(checkRow("err", "Memory", `${host.mem_available_mb} MB available is below the ${host.mem_guard_mb} MB guard.`));
    }

    const diskDetail = host.disk_free_gb === null || host.disk_free_gb === undefined
      ? `Disk usage was unreadable; at least ${host.disk_block_gb} GB free is required.`
      : `${host.disk_free_gb} GB free of ${host.disk_total_gb} GB; block below ${host.disk_block_gb} GB, warn below ${host.disk_warn_gb} GB.`;
    const diskKind = !host.disk_ok ? "err" : (host.disk_warn ? "warn" : "ok");
    panel.appendChild(checkRow(diskKind, "Disk", diskDetail));
    panel.appendChild(checkRow(host.job_lock_held ? "err" : "ok", "Job lock", host.job_lock_held ? "A dashboard job lock is currently held." : "No dashboard job lock is held."));
  }

  const deployment = state.deployment;
  if (deployment) {
    const summary = deployment.summary || {};
    panel.appendChild(checkRow(
      statusFromSummary(summary),
      "Deployment",
      `${summary.ok || 0} OK, ${summary.fail || 0} FAIL, ${summary.warn || 0} WARN, ${summary.skip || 0} SKIP.`,
    ));
    for (const row of deployment.blocking || []) {
      panel.appendChild(checkRow("err", `${row.group}: ${row.name}`, row.detail || "Deployment check failed."));
    }
  }

  return panel;
}

function buildForm(state, rerender) {
  const form = el("form", { class: "stack" });
  const result = el("div", {});
  const checksHost = el("div", {});
  const effectiveHoldHost = el("div", {});
  const inputs = {};
  let qgcDirty = false;

  for (const group of FIELD_GROUPS) {
    const fieldset = el("fieldset", { class: "field-group" }, [el("legend", { text: group.legend })]);
    const grid = el("div", { class: "field-grid" });
    for (const [name, type, help] of group.fields) {
      const field = fieldNode(name, type, help);
      inputs[name] = field.input;
      grid.appendChild(field.node);
    }
    fieldset.appendChild(grid);
    if (group.legend === "Advanced") {
      form.appendChild(el("details", {}, [el("summary", { text: "Advanced" }), fieldset]));
    } else {
      form.appendChild(fieldset);
    }
  }

  function applyHostDefaults() {
    if (!qgcDirty && state.host?.qgc_ip_default) {
      inputs.qgc_ip.value = state.host.qgc_ip_default;
    }
  }

  inputs.qgc_ip.addEventListener("input", () => {
    qgcDirty = true;
  });
  applyHostDefaults();

  function refreshEffectiveHoldPreview() {
    effectiveHoldHost.replaceChildren(renderEffectiveHoldPreview(state, inputs));
  }

  for (const key of ["hover_s", "gnss_loss_after_takeoff_s", "post_loss_hover_s"]) {
    inputs[key].addEventListener("input", refreshEffectiveHoldPreview);
  }

  const launchButton = el("button", { class: "btn-primary", type: "submit", text: "Launch" });
  const launchReason = el("span", { class: "help" });

  function updateLaunchState(submitting = false) {
    const deploymentBlocked = state.deployment ? (state.deployment.blocking || []).length > 0 : true;
    const jobLockBlocked = state.host ? state.host.job_lock_held === true : true;
    const memoryBlocked = state.host ? state.host.mem_ok === false && !inputs.ignore_memory_guard.checked : true;
    const diskBlocked = state.host ? state.host.disk_ok !== true : true;
    const checksBlocked = state.checksLoading || deploymentBlocked || jobLockBlocked || memoryBlocked || diskBlocked;

    launchButton.disabled = submitting || checksBlocked;
    if (state.checksLoading) launchReason.textContent = "running pre-flight checks...";
    else if (state.checkError && (!state.host || !state.deployment)) launchReason.textContent = "pre-flight checks failed.";
    else if (deploymentBlocked) launchReason.textContent = `${(state.deployment?.blocking || []).length} deployment failure(s) must be fixed.`;
    else if (jobLockBlocked) launchReason.textContent = "job lock is held.";
    else if (memoryBlocked) launchReason.textContent = "memory is below the guard.";
    else if (diskBlocked) launchReason.textContent = state.host?.disk_free_gb === null || state.host?.disk_free_gb === undefined
      ? "disk free space is unreadable; free space must be checked before launching."
      : `${state.host.disk_free_gb} GB free is below the ${state.host.disk_block_gb} GB disk guard; free disk space before launching.`;
    else launchReason.textContent = "ready to launch.";
  }

  function refreshChecksPanel() {
    checksHost.replaceChildren(renderChecks(state, inputs, async () => {
      state.checksLoading = true;
      state.checkError = "";
      refreshChecksPanel();
      try {
        await postJSON("/api/checks/deployment/refresh", {});
        const [host, deployment] = await Promise.all([getJSON("/api/host"), getJSON("/api/checks/deployment")]);
        state.host = host;
        state.deployment = deployment;
        applyHostDefaults();
      } catch (e) {
        state.checkError = e.status === 409 ? "Re-check refused while a dashboard job lock is held." : "Pre-flight refresh failed: " + ((e && e.message) || e);
      } finally {
        state.checksLoading = false;
        refreshChecksPanel();
      }
    }));
    updateLaunchState();
  }

  inputs.ignore_memory_guard.addEventListener("change", refreshChecksPanel);

  form.appendChild(effectiveHoldHost);
  refreshEffectiveHoldPreview();
  form.appendChild(checksHost);
  refreshChecksPanel();
  form.appendChild(el("div", { class: "launch-actions" }, [launchButton, launchReason]));
  form.appendChild(result);
  updateLaunchState();

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    updateLaunchState(true);
    result.replaceChildren();
    const body = {
      scenario: state.selected.name,
      no_global_position_gate: inputs.no_global_position_gate.checked,
      ignore_memory_guard: inputs.ignore_memory_guard.checked,
    };
    for (const key of [
      "hover_s",
      "startup_timeout_s",
      "world_ready_timeout_s",
      "land_timeout_s",
      "gnss_loss_after_takeoff_s",
      "post_loss_hover_s",
      "global_position_timeout_s",
      "global_position_stable_s",
    ]) addParsed(body, form, key, parseFloat);
    addParsed(body, form, "gnss_start_used", (v) => parseInt(v, 10));
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
      updateLaunchState(false);
    }
  });

  form.applyHostDefaults = applyHostDefaults;
  return form;
}

export async function renderLaunch() {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));

  const state = {
    scenarios: [],
    selected: null,
    selectedDetail: null,
    selectedDetailLoading: false,
    selectedDetailError: "",
    selectedDetailRequest: 0,
    query: "",
    host: null,
    deployment: null,
    checksLoading: true,
    checkError: "",
  };
  const initialScenario = new URLSearchParams(location.search).get("scenario");

  try {
    state.scenarios = await getJSON("/api/scenarios");
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load scenarios: " + ((e && e.message) || e) }));
    return;
  }

  if (initialScenario) {
    state.selected = state.scenarios.find((scenario) => scenario.name === initialScenario) || { name: initialScenario };
  }

  async function loadSelectedScenarioDetail(name) {
    const request = ++state.selectedDetailRequest;
    state.selectedDetail = null;
    state.selectedDetailError = "";
    state.selectedDetailLoading = true;
    render();
    try {
      const detail = await getJSON(`/api/scenarios/${encodeURIComponent(name)}`);
      if (request !== state.selectedDetailRequest) return;
      state.selectedDetail = detail;
    } catch (e) {
      if (request !== state.selectedDetailRequest) return;
      state.selectedDetailError = "Failed to load selected scenario timing: " + ((e && e.message) || e);
    } finally {
      if (request !== state.selectedDetailRequest) return;
      state.selectedDetailLoading = false;
      render();
    }
  }

  function selectScenario(scenario) {
    state.selected = scenario;
    state.selectedDetail = null;
    state.selectedDetailError = "";
    history.replaceState(null, "", `/launch?scenario=${encodeURIComponent(scenario.name)}`);
    loadSelectedScenarioDetail(scenario.name);
  }

  function changeScenario() {
    state.selected = null;
    state.selectedDetail = null;
    state.selectedDetailError = "";
    state.selectedDetailLoading = false;
    state.selectedDetailRequest += 1;
    history.replaceState(null, "", "/launch");
    render();
  }

  function render() {
    const children = [el("h1", { text: "Launch Flight" })];
    if (!state.selected) {
      const search = el("input", { type: "text", placeholder: "search name / run / description / vehicle...", value: state.query });
      search.addEventListener("input", () => {
        state.query = search.value;
        render();
      });
      children.push(
        el("section", { class: "stack" }, [
          el("div", { class: "field" }, [el("label", { for: "scenario-search", text: "Scenario" }), search, el("p", { class: "help", text: `${state.scenarios.length} scenarios; sorted alphabetically.` })]),
          renderScenarioList(state, selectScenario),
        ]),
      );
    } else {
      const form = buildForm(state, render);
      children.push(
        el("section", { class: "stack" }, [
          el("div", { class: "cluster" }, [
            el("h2", { text: state.selected.name }),
            el("button", { class: "btn-ghost", type: "button", text: "change scenario" }),
          ]),
          form,
        ]),
      );
      children[1].querySelector("button").addEventListener("click", changeScenario);
      form.applyHostDefaults();
    }
    app.replaceChildren(...children);
  }

  render();
  if (state.selected) loadSelectedScenarioDetail(state.selected.name);

  try {
    const [host, deployment] = await Promise.all([getJSON("/api/host"), getJSON("/api/checks/deployment")]);
    state.host = host;
    state.deployment = deployment;
  } catch (e) {
    state.checkError = "Pre-flight checks failed: " + ((e && e.message) || e);
  } finally {
    state.checksLoading = false;
    render();
  }
}
