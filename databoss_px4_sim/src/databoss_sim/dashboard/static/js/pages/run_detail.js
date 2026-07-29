import { el, kv, tabs } from "../dom.js";
import { getJSON } from "../api.js";
import { buildFilesView, buildGallery } from "../files_view.js";

const GZ_PROXY_CMD = "venv/bin/python scripts/sim/gz_websocket_enum_patch_proxy.py --listen-port 9002 --upstream ws://127.0.0.1:9003";
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]);

function isEmptyValue(value) {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

function imageEntry(entry) {
  const lower = (entry.name || entry.path || "").toLowerCase();
  const dot = lower.lastIndexOf(".");
  const ext = dot >= 0 ? lower.slice(dot) : "";
  return entry.kind === "image" && IMAGE_EXTENSIONS.has(ext);
}

function formatStatValue(value) {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") return String(value);
  return String(value);
}

function statTable(title, data) {
  const rows = Object.entries(data || {}).filter(([, value]) => !isEmptyValue(value));
  if (!rows.length) return null;
  const table = el("table", {}, [el("tr", {}, [el("th", { text: title }), el("th", { text: "value" })])]);
  for (const [key, value] of rows) {
    if (isEmptyValue(value)) continue;
    table.appendChild(el("tr", {}, [el("td", { text: key }), el("td", { text: formatStatValue(value) })]));
  }
  return el("div", { class: "table-scroll" }, [table]);
}

function statsGroup(title, data) {
  if (!data || typeof data !== "object") return null;
  const direct = [];
  const nested = [];
  for (const [key, value] of Object.entries(data)) {
    if (isEmptyValue(value)) continue;
    if (typeof value === "object" && !Array.isArray(value)) nested.push([key, value]);
    else direct.push([key, formatStatValue(value)]);
  }
  if (!direct.length && !nested.length) return null;

  const children = [el("h2", { text: title })];
  if (direct.length) children.push(kv(direct));
  for (const [key, value] of nested) {
    const table = statTable(key, value);
    if (table) children.push(table);
  }
  return el("section", { class: "tile stack stats-group" }, children);
}

async function renderStats(runId) {
  let stats;
  try {
    stats = await getJSON(`/artifacts/runs/${encodeURIComponent(runId)}/run_stats.json`);
  } catch (e) {
    return el("p", { class: "empty", text: "Stats have not been generated for this run." });
  }

  const groups = [
    statsGroup("Flight", stats.flight),
    statsGroup("Accuracy", stats.accuracy),
    statsGroup("GNSS", stats.gnss),
    statsGroup("Flow", stats.flow),
  ].filter(Boolean);
  if (!groups.length) return el("p", { class: "empty", text: "Stats have not been generated for this run." });
  return el("div", { class: "stats-grid" }, groups);
}

function renderConnectionLinks(conn) {
  const wrap = el("div", {});
  wrap.appendChild(kv([
    ["QGC enabled", conn.qgc_enabled], ["QGC ip", conn.qgc_ip],
    ["QGC local UDP port", conn.qgc_local_port], ["QGC remote UDP port", conn.qgc_remote_port],
    ["QGC source", conn.qgc_source], ["QGC target", "UDP MAVLink target, not a URL"],
    ["gz-web enabled", conn.gazebo_web_enabled], ["gz-web raw bridge port", conn.gazebo_web_port || 9003],
    ["gz-web raw bridge note", "9003 is reference only; browsers should use the 9002 enum-patch proxy"],
    ["gz-web publication Hz", conn.gazebo_web_publication_hz],
    ["viewer", "https://app.gazebosim.org/visualization"],
    ["paste URL", "ws://localhost:9002"],
    ["SSH tunnel", "ssh -N -L 9002:127.0.0.1:9002 root@100.78.93.35"],
    ["proxy start command", GZ_PROXY_CMD],
    ["flow_bridge enabled", conn.flow_bridge_enabled], ["estimator", conn.flow_bridge_estimator],
    ["flow_bridge rate Hz", conn.flow_bridge_rate_hz], ["axis_map", conn.axis_map],
    ["hfov_rad", conn.hfov_rad],
    ["EKF2_OF_CTRL", conn.ekf2_of_ctrl], ["EKF2_OF_QMIN", conn.ekf2_of_qmin],
    ["EKF2_OF_N_MIN", conn.ekf2_of_n_min], ["EKF2_OF_DELAY", conn.ekf2_of_delay],
    ["aiding mode", conn.aiding_mode], ["EKF2_EV_CTRL", conn.ekf2_ev_ctrl],
  ]));
  wrap.appendChild(el("p", {}, [
    el("a", { href: "https://app.gazebosim.org/visualization", text: "Open Gazebo visualization" }),
  ]));
  return wrap;
}

function artifactList(run) {
  const entries = Object.entries(run.artifacts || {}).filter(([, v]) => v);
  if (!entries.length) return el("p", { class: "empty", text: "No artifacts recorded for this run." });
  return el("div", { class: "list" }, entries.map(([k, v]) =>
    el("a", { class: "list-row", href: `/artifacts/runs/${encodeURIComponent(run.run_id)}/${v}` }, [
      el("div", {}, [
        el("div", { class: "row-main", text: k }),
        el("div", { class: "row-meta" }, [el("span", { text: v })]),
      ]),
      el("span", { class: "row-chevron", text: ">" }),
    ])
  ));
}

function comparisonList(comparisons) {
  if (!comparisons.length) return el("p", { class: "empty", text: "No comparisons reference this run." });
  return el("div", { class: "list" }, comparisons.map((comparisonId) =>
    el("a", { class: "list-row", href: `/comparisons/${encodeURIComponent(comparisonId)}` }, [
      el("div", { class: "row-main", text: comparisonId }),
      el("span", { class: "row-chevron", text: ">" }),
    ])
  ));
}

function warningList(warnings) {
  if (!warnings.length) return el("p", { class: "empty", text: "No warnings recorded for this run." });
  return el("div", { class: "list" }, warnings.map((warning) =>
    el("div", { class: "list-row" }, [
      el("div", { class: "row-main", text: warning }),
      el("span", { class: "dot dot-warn" }),
    ])
  ));
}

export async function renderRun(runId) {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(el("span", { class: "spinner" }));
  app.appendChild(document.createTextNode("loading..."));

  let run;
  try {
    run = await getJSON(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("div", { class: "error-box", text: "Run not found: " + runId }));
    return;
  }

  app.innerHTML = "";
  app.appendChild(el("p", {}, [el("a", { href: "/runs", text: "Back to runs" })]));
  app.appendChild(el("h1", { text: run.run_id }));

  // Fetched once, lazily, on first activation of either Files or Plots -
  // Plots filters this same tree instead of making a second API call.
  let filesPromise = null;
  const getFiles = () => {
    if (!filesPromise) {
      filesPromise = getJSON(`/api/runs/${encodeURIComponent(run.run_id)}/files`);
    }
    return filesPromise;
  };

  const sections = [
    {
      label: "Overview",
      render: () => kv([
        ["phase", run.phase], ["scenario_name", run.scenario_name],
        ["algorithm", run.algorithm], ["gnss_state", run.gnss_state],
        ["world_variant", run.world_variant], ["tag_source", run.tag_source],
        ["contract_status", run.contract_status], ["accepted", run.accepted],
        ["created_utc", run.created_utc], ["last_modified_utc", run.last_modified_utc],
      ]),
    },
    {
      label: "Metrics",
      render: () => kv(Object.entries(run.key_metrics)),
    },
    {
      label: "Connections",
      render: () => {
        return renderConnectionLinks(run.connections);
      },
    },
    {
      label: "Artifacts",
      render: () => artifactList(run),
    },
    {
      label: "Files",
      render: async () => buildFilesView(await getFiles()),
    },
    {
      label: "Stats",
      render: async () => renderStats(run.run_id),
    },
    {
      label: "Plots",
      render: async () => {
        const entries = await getFiles();
        const plots = entries.filter(e => (e.dir === "plots" || e.dir.startsWith("plots/")) && imageEntry(e));
        return buildGallery(plots, "No plots recorded for this run.");
      },
    },
  ];

  if (run.comparisons.length) {
    sections.push({
      label: `Comparisons (${run.comparisons.length})`,
      render: () => comparisonList(run.comparisons),
    });
  }

  if (run.warnings.length) {
    sections.push({
      label: `Warnings (${run.warnings.length})`,
      render: () => warningList(run.warnings),
    });
  }

  app.appendChild(tabs(sections));
}
