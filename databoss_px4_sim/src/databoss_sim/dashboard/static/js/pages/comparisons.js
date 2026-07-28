import { el } from "../dom.js";
import { getJSON } from "../api.js";

const COLUMNS = ["comparison_id", "title", "case_count", "has_report_md", "warnings"];

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
    placeholder: "search comparison_id / title...",
    value: state.q,
  });
  const summary = el("p", {});
  const tableWrap = el("div", { class: "table-scroll" });

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
    tableWrap.replaceChildren();
    syncUrl();
    if (!filtered.length) {
      tableWrap.appendChild(el("p", {
        class: "help",
        text: comparisons.length ? "No comparisons match the current search." : "No comparisons have been indexed yet.",
      }));
      return;
    }

    const table = el("table", {});
    const headRow = el("tr", {});
    for (const key of COLUMNS) {
      const arrow = state.sortKey === key ? (state.sortDir === 1 ? " ▲" : " ▼") : "";
      const th = el("th", { class: "sortable", text: key + arrow });
      th.addEventListener("click", () => {
        if (state.sortKey === key) state.sortDir = -state.sortDir;
        else {
          state.sortKey = key;
          state.sortDir = 1;
        }
        render();
      });
      headRow.appendChild(th);
    }
    table.appendChild(headRow);

    for (const c of filtered) {
      table.appendChild(el("tr", {}, [
        el("td", {}, [el("a", { href: `/comparisons/${encodeURIComponent(c.comparison_id)}`, text: c.comparison_id })]),
        el("td", { text: c.title || "" }),
        el("td", { text: String(c.case_count ?? "") }),
        el("td", { text: c.has_report_md ? "yes" : "no" }),
        el("td", { text: String((c.warnings || []).length) }),
      ]));
    }
    tableWrap.appendChild(table);
  }

  searchInput.addEventListener("input", () => {
    state.q = searchInput.value;
    render();
  });
  app.replaceChildren(el("div", { class: "filters" }, [searchInput]), summary, tableWrap);
  render();
}
