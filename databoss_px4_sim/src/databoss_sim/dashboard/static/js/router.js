import { renderList } from "./pages/runs.js";
import { renderRun } from "./pages/run_detail.js";
import { renderComparison } from "./pages/comparison_detail.js";
import { renderComparisons } from "./pages/comparisons.js";
import { renderScenarios, renderScenario } from "./pages/scenarios.js";
import { renderHealth } from "./pages/health.js";
import { renderCreate } from "./pages/create.js";
import { renderJobs } from "./pages/jobs.js";
import { renderJob } from "./pages/job_detail.js";

function updateNavActive() {
  const path = location.pathname;
  document.querySelectorAll("nav a").forEach((a) => {
    const href = a.getAttribute("href");
    const active = href === "/" ? path === "/" : path.startsWith(href);
    a.classList.toggle("active", active);
  });
}

function route() {
  updateNavActive();
  const path = location.pathname;
  const runMatch = path.match(/^\/runs\/(.+)$/);
  const jobMatch = path.match(/^\/jobs\/(.+)$/);
  const compMatch = path.match(/^\/comparisons\/(.+)$/);
  const scenarioMatch = path.match(/^\/scenarios\/(.+)$/);
  if (path === "/create") return renderCreate();
  if (path === "/jobs") return renderJobs();
  if (runMatch) return renderRun(decodeURIComponent(runMatch[1]));
  if (jobMatch) return renderJob(decodeURIComponent(jobMatch[1]));
  if (compMatch) return renderComparison(decodeURIComponent(compMatch[1]));
  if (path === "/comparisons") return renderComparisons();
  if (scenarioMatch) return renderScenario(decodeURIComponent(scenarioMatch[1]));
  if (path === "/scenarios") return renderScenarios();
  if (path === "/health") return renderHealth();
  return renderList();
}

route();
