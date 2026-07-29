import { el } from "../dom.js";
import { getJSON, postJSON } from "../api.js";

function badgeFor(kind, status) {
  const pass = (kind === "model_sync" && status === "IN_SYNC") ||
    (kind === "fov_consistency" && status === "MATCH");
  return el("span", { class: `badge ${pass ? "badge-ok" : "badge-err"}`, text: pass ? "pass" : "fail" });
}

function formatValue(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
  if (typeof value === "string") return value;
  const text = JSON.stringify(value);
  return text && text.length > 120 ? text.slice(0, 117) + "..." : text;
}

function modelSyncTable(rows) {
  const table = el("table", {});
  table.appendChild(el("tr", {}, ["result", "model", "status", "diff files"].map((h) => el("th", { text: h }))));
  for (const row of rows || []) {
    table.appendChild(el("tr", {}, [
      el("td", {}, [badgeFor("model_sync", row.status)]),
      el("td", { text: row.model || "" }),
      el("td", { text: row.status || "" }),
      el("td", { text: Object.keys(row.diffs || {}).join(", ") }),
    ]));
  }
  return table;
}

function fovTable(rows) {
  const table = el("table", {});
  table.appendChild(el("tr", {}, [
    "result", "scenario", "vehicle_model", "camera_submodel", "declared_hfov_rad", "actual_hfov_rad", "status",
  ].map((h) => el("th", { text: h }))));
  for (const row of rows || []) {
    table.appendChild(el("tr", {}, [
      el("td", {}, [badgeFor("fov_consistency", row.status)]),
      el("td", { text: row.scenario || "" }),
      el("td", { text: row.vehicle_model || "" }),
      el("td", { text: row.camera_submodel || "" }),
      el("td", { text: formatValue(row.declared_hfov_rad) }),
      el("td", { text: formatValue(row.actual_hfov_rad) }),
      el("td", { text: row.status || "" }),
    ]));
  }
  return table;
}

function renderResults(data) {
  const wrap = el("div", {});
  const modelRows = data.model_sync || [];
  const fovRows = data.fov_consistency || [];
  wrap.appendChild(el("h2", { text: `Model sync (${modelRows.length})` }));
  if (modelRows.length) {
    wrap.appendChild(el("div", { class: "table-scroll" }, [modelSyncTable(modelRows)]));
  } else {
    wrap.appendChild(el("p", { class: "empty", text: "No model sync results returned." }));
  }
  wrap.appendChild(el("h2", { text: `FOV consistency (${fovRows.length})` }));
  if (fovRows.length) {
    wrap.appendChild(el("div", { class: "table-scroll" }, [fovTable(fovRows)]));
  } else {
    wrap.appendChild(el("p", { class: "empty", text: "No flow-enabled scenarios returned." }));
  }
  return wrap;
}

export async function renderHealth() {
  const app = document.getElementById("app");
  const refreshBtn = el("button", { class: "btn", type: "button", text: "Re-run check" });
  const errorHost = el("div", {});
  const resultHost = el("div", {});

  async function load() {
    resultHost.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));
    try {
      const data = await getJSON("/api/checks/model_sync");
      errorHost.replaceChildren();
      resultHost.replaceChildren(renderResults(data));
    } catch (e) {
      resultHost.replaceChildren();
      errorHost.replaceChildren(el("div", { class: "error-box", text: "Failed to load health checks: " + ((e && e.message) || e) }));
    }
  }

  refreshBtn.addEventListener("click", async () => {
    refreshBtn.disabled = true;
    errorHost.replaceChildren();
    resultHost.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("running check..."));
    try {
      const data = await postJSON("/api/checks/model_sync/refresh", {});
      resultHost.replaceChildren(renderResults(data));
    } catch (e) {
      const msg = e.status === 409 ? "Refusing to re-run while a dashboard job lock is held." : "Refresh failed: " + ((e && e.message) || e);
      errorHost.replaceChildren(el("div", { class: "error-box", text: msg }));
    } finally {
      refreshBtn.disabled = false;
    }
  });

  app.replaceChildren(
    el("div", { class: "job-actions" }, [el("h1", { text: "Health" }), refreshBtn]),
    errorHost,
    resultHost,
  );
  await load();
}
