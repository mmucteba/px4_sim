import { el, kv } from "../dom.js";
import { getJSON } from "../api.js";

export async function renderComparison(comparisonId) {
  const app = document.getElementById("app");
  let comp;
  try {
    comp = await getJSON(`/api/comparisons/${encodeURIComponent(comparisonId)}`);
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("p", { text: "Comparison not found: " + comparisonId }));
    return;
  }
  app.innerHTML = "";
  app.appendChild(el("p", {}, [el("a", { href: "/", text: "< back to run list" })]));
  app.appendChild(el("h2", { text: comp.title }));
  app.appendChild(kv([
    ["comparison_id", comp.comparison_id], ["case_count", comp.case_count],
  ]));
  if (comp.has_report_md) {
    app.appendChild(el("p", {}, [
      el("a", { href: `/artifacts/comparisons/${comp.comparison_id}/report.md`, text: "view report.md (raw)" }),
    ]));
  }
  app.appendChild(el("h2", { text: "Runs in this comparison" }));
  app.appendChild(el("ul", {}, comp.run_ids.map(rid =>
    el("li", {}, [el("a", { href: `/runs/${rid}`, text: rid })])
  )));
}
