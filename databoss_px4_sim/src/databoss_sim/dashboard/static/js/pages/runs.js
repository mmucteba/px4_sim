import { el } from "../dom.js";
import { getJSON } from "../api.js";

export async function renderList() {
  const app = document.getElementById("app");
  const runs = await getJSON("/api/runs");
  const params = new URLSearchParams(location.search);
  const fAlgo = params.get("algorithm") || "";
  const fGnss = params.get("gnss_state") || "";
  const fStatus = params.get("status") || "";

  const filtered = runs.filter(r =>
    (!fAlgo || r.algorithm === fAlgo) &&
    (!fGnss || r.gnss_state === fGnss) &&
    (!fStatus || r.contract_status === fStatus)
  );

  app.innerHTML = "";
  const summary = el("p", { text: `${filtered.length} of ${runs.length} runs (filters: algorithm=${fAlgo||"any"}, gnss_state=${fGnss||"any"}, status=${fStatus||"any"})` });
  app.appendChild(summary);

  const table = el("table", {});
  const head = el("tr", {}, [
    el("th", { text: "run_id" }), el("th", { text: "phase" }), el("th", { text: "algorithm" }),
    el("th", { text: "gnss_state" }), el("th", { text: "status" }), el("th", { text: "accepted" }),
    el("th", { text: "horiz err max (m)" }),
  ]);
  table.appendChild(head);
  for (const r of filtered.slice(0, 500)) {
    const row = el("tr", {}, [
      el("td", {}, [el("a", { href: `/runs/${r.run_id}`, text: r.run_id })]),
      el("td", { text: r.phase || "" }),
      el("td", { text: r.algorithm || "" }),
      el("td", { text: r.gnss_state || "" }),
      el("td", { text: r.contract_status, class: `status-${r.contract_status}` }),
      el("td", { text: r.accepted === null ? "" : String(r.accepted) }),
      el("td", { text: r.key_metrics.horizontal_error_max_m != null ? r.key_metrics.horizontal_error_max_m.toFixed(3) : "" }),
    ]);
    table.appendChild(row);
  }
  app.appendChild(table);
}
