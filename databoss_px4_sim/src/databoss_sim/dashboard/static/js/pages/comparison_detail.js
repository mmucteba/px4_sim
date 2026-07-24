import { el, kv, tabs } from "../dom.js";
import { getJSON } from "../api.js";

export async function renderComparison(comparisonId) {
  const app = document.getElementById("app");
  app.innerHTML = "";
  app.appendChild(el("span", { class: "spinner" }));
  app.appendChild(document.createTextNode("loading..."));

  let comp;
  try {
    comp = await getJSON(`/api/comparisons/${encodeURIComponent(comparisonId)}`);
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("div", { class: "error-box", text: "Comparison not found: " + comparisonId }));
    return;
  }

  app.innerHTML = "";
  app.appendChild(el("p", {}, [el("a", { href: "/", text: "< back to run list" })]));
  app.appendChild(el("h1", { text: comp.title }));

  const sections = [
    {
      label: "Overview",
      render: () => {
        const wrap = el("div", {});
        wrap.appendChild(kv([
          ["comparison_id", comp.comparison_id], ["case_count", comp.case_count],
        ]));
        if (comp.has_report_md) {
          wrap.appendChild(el("p", {}, [
            el("a", { href: `/artifacts/comparisons/${comp.comparison_id}/report.md`, text: "view report.md (raw)" }),
          ]));
        }
        return wrap;
      },
    },
    {
      label: `Runs (${comp.run_ids.length})`,
      render: () => el("ul", {}, comp.run_ids.map(rid =>
        el("li", {}, [el("a", { href: `/runs/${rid}`, text: rid })])
      )),
    },
  ];

  app.appendChild(tabs(sections));
}
