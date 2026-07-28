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
      label: "Report",
      render: async () => {
        const { html } = await getJSON(`/api/comparisons/${encodeURIComponent(comp.comparison_id)}/report_html`);
        const wrap = el("div", {});
        if (html) {
          const reportDiv = el("div", { class: "report-html" });
          // Safe because report_rendering.py applies _sanitize_html before returning report HTML.
          reportDiv.innerHTML = html;
          wrap.appendChild(reportDiv);
        } else {
          wrap.appendChild(el("p", { class: "help", text: "No report.md for this comparison." }));
        }
        wrap.appendChild(kv([
          ["comparison_id", comp.comparison_id], ["case_count", comp.case_count],
        ]));
        if (comp.has_report_md) {
          wrap.appendChild(el("p", {}, [
            el("a", { href: `/artifacts/comparisons/${comp.comparison_id}/report.md`, text: "view report.md (raw)" }),
          ]));
        }
        if (comp.has_summary_csv) {
          wrap.appendChild(el("p", {}, [
            el("a", { href: `/artifacts/comparisons/${comp.comparison_id}/summary.csv`, text: "view summary.csv (raw)" }),
          ]));
        }
        return wrap;
      },
    },
    {
      label: `Cases (${comp.run_ids.length})`,
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
