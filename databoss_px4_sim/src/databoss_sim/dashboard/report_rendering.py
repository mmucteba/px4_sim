"""Render a comparison's report.md to HTML for the dashboard's Report tab.

9/34 real comparisons have no report.md at all (older/legacy folders) -
callers must treat None as a normal case, not an error. Two rewrites are
applied to the raw markdown before conversion, both confirmed necessary
by reading every real report.md in the repo:

1. Local absolute filesystem links to a run
   ([text](/opt/databoss_px4_sim/experiments/runs/<id>)) become the
   dashboard route ([text](/runs/<id>)) - the rendered HTML is injected
   into the SPA at /comparisons/<id>, where a raw filesystem path is
   useless to a browser.
2. Relative image references (![alt](plots/foo.png),
   ![alt](camera_samples/x/frame.jpg)) become absolute /artifacts URLs -
   same class of bug already fixed once this session for the
   terrain-generator proxy: a relative path resolves against whatever
   route served the page, not against report.md's real location on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

import markdown

from databoss_sim.dashboard.config import PROJECT_ROOT

_RUN_LINK_PATTERN = re.compile(re.escape(f"]({PROJECT_ROOT}/experiments/runs/") + r"([^)]+)\)")
_RELATIVE_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\((plots|camera_samples)/([^)]+)\)")


def render_report_html(comparison_dir: Path, comparison_id: str) -> str | None:
    report_path = comparison_dir / "report.md"
    if not report_path.is_file():
        return None

    text = report_path.read_text()
    text = _RUN_LINK_PATTERN.sub(lambda m: f"](/runs/{m.group(1)})", text)
    text = _RELATIVE_IMAGE_PATTERN.sub(
        lambda m: f"![{m.group(1)}](/artifacts/comparisons/{comparison_id}/{m.group(2)}/{m.group(3)})",
        text,
    )
    return markdown.markdown(text, extensions=["tables", "fenced_code"])
