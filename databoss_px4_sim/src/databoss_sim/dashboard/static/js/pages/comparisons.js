import { el } from "../dom.js";
import { getJSON } from "../api.js";

const COLUMNS = ["comparison_id", "title", "case_count", "has_report_md", "warnings"];

function present(value) {
  return value !== null && value !== undefined && value !== "";
}

function comparisonRow(c) {
  const left = [el("div", { class: "row-main", text: c.comparison_id })];
  if (present(c.title)) {
    left.push(el("div", { class: "row-meta" }, [el("span", { text: c.title })]));
  }
  const right = [
    el("span", { class: "row-metric", text: `${c.case_count ?? 0} cases` }),
    el("span", { class: c.has_report_md ? "badge badge-ok" : "badge badge-caution", text: c.has_report_md ? "report.md" : "no report.md" }),
    el("span", { class: "row-metric", text: `${(c.warnings || []).length} warnings` }),
    el("span", { class: "row-chevron", text: ">" }),
  ];
  return el("a", { class: "list-row archive-row", href: `/comparisons/${encodeURIComponent(c.comparison_id)}` }, [
    el("div", {}, left),
    el("div", { class: "cluster archive-row-tail" }, right),
  ]);
}

export async function renderComparisons() {
  const app = document.getElementById("app");
  app.replaceChildren(el("span", { class: "spinner" }), document.createTextNode("loading..."));

  let comparisons;
  try {
    comparisons = await getJSON("/api/comparisons");
  } catch (e) {
    app.replaceChildren(el("div", { class: "error-box", text: "Failed to load comparisons: " + ((e && e.message) || e) }));
    return;
  }

  const params = new URLSearchParams(location.search);
  const state = { q: params.get("q") || "", sortKey: "comparison_id", sortDir: 1 };
  const searchInput = el("input", {
    type: "text",
    "aria-label": "search comparisons",
    placeholder: "search comparison_id / title...",
    value: state.q,
  });
  const summary = el("p", {});
  const sortHost = el("div", { class: "cluster sort-controls" });
  const listHost = el("div", { class: "list" });

  function syncUrl() {
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    history.replaceState(null, "", p.toString() ? `/comparisons?${p}` : "/comparisons");
  }

  function sortValue(row, key) {
    if (key === "warnings") return (row.warnings || []).length;
    return row[key];
  }

  function render() {
    const q = state.q.trim().toLowerCase();
    const filtered = comparisons.filter((c) =>
      !q ||
      (c.comparison_id && c.comparison_id.toLowerCase().includes(q)) ||
      (c.title && c.title.toLowerCase().includes(q))
    );
    filtered.sort((a, b) => {
      const av = sortValue(a, state.sortKey);
      const bv = sortValue(b, state.sortKey);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av < bv) return -state.sortDir;
      if (av > bv) return state.sortDir;
      return 0;
    });

    summary.textContent = `${filtered.length} of ${comparisons.length} comparisons`;
    sortHost.replaceChildren(el("span", { class: "help", text: "Sort" }));
    syncUrl();
    for (const key of COLUMNS) {
      const active = state.sortKey === key;
      const arrow = active ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
      const button = el("button", {
        class: active ? "btn sort-active" : "btn-ghost",
        type: "button",
        text: key + arrow,
        "aria-pressed": active ? "true" : "false",
      });
      button.addEventListener("click", () => {
        if (state.sortKey === key) state.sortDir = -state.sortDir;
        else {
          state.sortKey = key;
          state.sortDir = 1;
        }
        render();
      });
      sortHost.appendChild(button);
    }

    listHost.replaceChildren();
    for (const c of filtered) {
      listHost.appendChild(comparisonRow(c));
    }
    if (!filtered.length) {
      listHost.appendChild(el("p", {
        class: "empty",
        text: comparisons.length ? "No comparisons match the current search." : "No comparisons have been indexed yet.",
      }));
    }
  }

  searchInput.addEventListener("input", () => {
    state.q = searchInput.value;
    render();
  });
  app.replaceChildren(el("div", { class: "filters" }, [searchInput]), sortHost, summary, listHost);
  render();
}
