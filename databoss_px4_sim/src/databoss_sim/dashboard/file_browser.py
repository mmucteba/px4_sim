"""Generic file-tree walker for the dashboard's Files/Plots tabs.

Real run/comparison layouts are highly inconsistent across phases
(plots/ empty in 150/181 runs, final_summary.md missing from every
phase16 run, comparison folders split "current" vs "legacy" layout with
no plots/ subdirectory at all) - so this never hardcodes an expected
filename or directory. It walks whatever actually exists and classifies
it by extension for the frontend to render appropriately. Metadata only;
file bytes are served by the existing /artifacts StaticFiles mount.
"""

from __future__ import annotations

from pathlib import Path

# Entries of a previewable kind (markdown/json/csv/text) above this size
# are flagged preview_disabled instead of being fetched inline - some CSVs
# and raw truth logs run 5-90M.
PREVIEW_SIZE_LIMIT_BYTES = 1_000_000

_PREVIEWABLE_KINDS = {"markdown", "json", "csv", "text"}

_KIND_BY_EXT = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".md": "markdown",
    ".json": "json",
    ".csv": "csv",
    ".txt": "text",
    ".log": "text",
    ".err": "text",
    ".yaml": "text",
    ".yml": "text",
}


def _classify(path: Path) -> str:
    return _KIND_BY_EXT.get(path.suffix.lower(), "binary")


def build_file_tree(root: Path, artifact_prefix: str) -> list[dict]:
    """Metadata-only walk of `root` (one run or comparison directory).

    `artifact_prefix` is the URL path already served by the /artifacts
    StaticFiles mount (e.g. "runs/<run_id>", "comparisons/<comparison_id>").
    """
    if not root.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue

        rel = path.relative_to(root).as_posix()
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        kind = _classify(path)

        entry: dict = {
            "name": path.name,
            "path": rel,
            "dir": parent,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "kind": kind,
            "url": f"/artifacts/{artifact_prefix}/{rel}",
        }
        if kind in _PREVIEWABLE_KINDS and stat.st_size > PREVIEW_SIZE_LIMIT_BYTES:
            entry["preview_disabled"] = True
        entries.append(entry)

    return entries
