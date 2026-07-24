import { renderList } from "./pages/runs.js";
import { renderRun } from "./pages/run_detail.js";
import { renderComparison } from "./pages/comparison_detail.js";
import { renderCreate } from "./pages/create.js";

function route() {
  const path = location.pathname;
  const runMatch = path.match(/^\/runs\/(.+)$/);
  const compMatch = path.match(/^\/comparisons\/(.+)$/);
  if (path === "/create") return renderCreate();
  if (runMatch) return renderRun(decodeURIComponent(runMatch[1]));
  if (compMatch) return renderComparison(decodeURIComponent(compMatch[1]));
  return renderList();
}

route();
