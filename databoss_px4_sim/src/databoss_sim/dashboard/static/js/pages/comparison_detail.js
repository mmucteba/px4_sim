import { el, kv, tabs } from "../dom.js";
import { getJSON } from "../api.js";
import { buildFilesView, buildGallery } from "../files_view.js";

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

  let filesPromise = null;
  const getFiles = () => {
    if (!filesPromise) {
      filesPromise = getJSON(`/api/comparisons/${encodeURIComponent(comp.comparison_id)}/files`);
    }
    return filesPromise;
  };

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
    {
      label: "Files",
      render: async () => buildFilesView(await getFiles()),
    },
    {
      label: "Plots",
      render: async () => {
        const entries = await getFiles();
        const plots = entries.filter(e =>
          e.dir === "plots" || e.dir.startsWith("plots/") ||
          e.dir === "camera_samples" || e.dir.startsWith("camera_samples/")
        );
        return buildGallery(plots, "No plots recorded for this comparison.");
      },
    },
  ];

  app.appendChild(tabs(sections));
}
