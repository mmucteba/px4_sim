#!/usr/bin/env python3
"""Build experiments/index.json from experiments/runs/ and experiments/comparisons/.

Phase 17A (absorbed Phase 13 scope). Never hand-edited - regenerate with:

    venv/bin/python scripts/analysis/build_experiments_index.py

Never crashes on a single bad/legacy run folder: every run gets an
IndexEntry, with parse failures recorded in that entry's `warnings` list
instead of aborting the whole build.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import yaml  # noqa: E402

from databoss_sim.contracts.comparison import load_comparison_manifest  # noqa: E402
from databoss_sim.contracts.connections import build_run_connections  # noqa: E402
from databoss_sim.contracts.ekf_metrics import EkfVsTruthMetrics  # noqa: E402
from databoss_sim.contracts.index_entry import (  # noqa: E402
    ComparisonIndexEntry,
    ExperimentsIndex,
    IndexEntry,
)
from databoss_sim.contracts.postprocess_summary import PostprocessSummary  # noqa: E402

RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"
COMPARISONS_DIR = PROJECT_ROOT / "experiments" / "comparisons"
INDEX_PATH = PROJECT_ROOT / "experiments" / "index.json"

PHASE_RE = re.compile(r"^\d{8}_\d{6}_(phase\w+?)_")
IN_PROGRESS_FRESHNESS_MINUTES = 60

SCHEMA_VERSION = 1


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open() as f:
        return json.load(f)


def _load_yaml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with path.open() as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dir_last_modified(run_dir: Path) -> datetime:
    """Latest mtime of any file directly under the run dir (non-recursive is
    enough - the runner writes its terminal artifacts at the top level)."""
    latest = run_dir.stat().st_mtime
    for child in run_dir.iterdir():
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return datetime.fromtimestamp(latest, tz=timezone.utc)


def build_reverse_comparison_index() -> tuple[dict[str, list[str]], dict[str, dict]]:
    """Returns (run_id -> [comparison_id, ...], run_id -> case-tag dict)."""
    run_to_comparisons: dict[str, list[str]] = {}
    run_to_tags: dict[str, dict] = {}

    if not COMPARISONS_DIR.is_dir():
        return run_to_comparisons, run_to_tags

    for comp_dir in sorted(COMPARISONS_DIR.iterdir()):
        manifest_path = comp_dir / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = load_comparison_manifest(manifest_path)
        except Exception:
            continue
        for case in manifest.cases:
            run_id = Path(case.run_dir).name
            run_to_comparisons.setdefault(run_id, []).append(comp_dir.name)
            # First manifest to tag a run wins - manifests are hand-curated
            # and authoritative, later duplicates shouldn't override.
            run_to_tags.setdefault(
                run_id,
                {
                    "algorithm": case.kind,
                    "gnss_state": case.gnss_state,
                    "world_variant": case.world_variant,
                },
            )

    return run_to_comparisons, run_to_tags


def classify_contract_status(run_dir: Path, last_modified: datetime) -> str:
    has_ekf = (run_dir / "ekf_vs_ground_truth_metrics.json").is_file()
    has_postprocess = (run_dir / "postprocess_summary.json").is_file()
    has_sensor_contract = (run_dir / "sensor_contract_report.json").is_file()

    if has_ekf and has_postprocess and has_sensor_contract:
        return "conformant"

    has_config = (run_dir / "config.yaml").is_file()
    has_readme = (run_dir / "README.md").is_file()
    has_any_terminal = has_ekf or has_postprocess
    if has_any_terminal or has_config or has_readme:
        # Missing one or more of the three "conformant" files but has some
        # recognizable content - either a legacy pre-Phase-12 run, or an
        # in-progress/incomplete one. Distinguish by freshness.
        age_minutes = (datetime.now(timezone.utc) - last_modified).total_seconds() / 60.0
        end_to_end_status = (run_dir / "end_to_end_status.json").is_file()
        terminal_present = has_ekf and has_postprocess
        if not terminal_present:
            if age_minutes <= IN_PROGRESS_FRESHNESS_MINUTES:
                return "in_progress"
            if not end_to_end_status:
                return "incomplete"
        return "legacy"

    return "incomplete"


def build_run_entry(
    run_dir: Path,
    run_to_comparisons: dict[str, list[str]],
    run_to_tags: dict[str, dict],
) -> IndexEntry:
    run_id = run_dir.name
    warnings: list[str] = []

    last_modified = _dir_last_modified(run_dir)
    contract_status = classify_contract_status(run_dir, last_modified)

    phase_match = PHASE_RE.match(run_id)
    phase = phase_match.group(1) if phase_match else None

    config = _load_yaml(run_dir / "config.yaml")
    scenario_name = None
    if config:
        scenario_name = (config.get("run") or {}).get("name")
    else:
        warnings.append("config.yaml missing or unparsable")

    tags = run_to_tags.get(run_id)
    if tags:
        algorithm = tags["algorithm"]
        gnss_state = tags["gnss_state"]
        world_variant = tags["world_variant"]
        tag_source = "comparison_manifest"
    else:
        algorithm = gnss_state = world_variant = None
        tag_source = "unknown"

    created_utc = None
    accepted = None
    key_metrics: dict[str, float | None] = {
        "horizontal_error_max_m": None,
        "horizontal_error_mean_m": None,
        "height_abs_error_max_m": None,
        "error_3d_max_m": None,
    }

    ekf_path = run_dir / "ekf_vs_ground_truth_metrics.json"
    ekf_raw = _load_json(ekf_path)
    if ekf_raw is not None:
        try:
            metrics = EkfVsTruthMetrics.model_validate(ekf_raw)
            created_utc = metrics.created_utc
            accepted = metrics.accepted
            key_metrics["horizontal_error_max_m"] = metrics.horizontal_error.max_m
            key_metrics["horizontal_error_mean_m"] = metrics.horizontal_error.mean_m
            key_metrics["height_abs_error_max_m"] = metrics.height_abs_error.max_m
            key_metrics["error_3d_max_m"] = metrics.error_3d.max_m
        except Exception as e:
            warnings.append(f"ekf_vs_ground_truth_metrics.json failed to validate: {e}")

    if accepted is None:
        pp_raw = _load_json(run_dir / "postprocess_summary.json")
        if pp_raw is not None:
            try:
                pp = PostprocessSummary.model_validate(pp_raw)
                accepted = pp.accepted
                created_utc = created_utc or pp.created_utc
            except Exception as e:
                warnings.append(f"postprocess_summary.json failed to validate: {e}")

    artifacts: dict[str, str] = {}
    for rel_name, key in [
        ("config.yaml", "config"),
        ("final_summary.md", "final_summary"),
        ("ekf_vs_ground_truth_metrics.json", "ekf_metrics_json"),
        ("sensor_contract_report.json", "sensor_contract_json"),
        ("postprocess_summary.json", "postprocess_summary_json"),
        ("logs/flight.ulg.gz", "ulog"),
        ("plots", "plots_dir"),
    ]:
        if (run_dir / rel_name).exists():
            artifacts[key] = rel_name

    try:
        connections = build_run_connections(run_dir)
    except Exception as e:
        warnings.append(f"connections snapshot failed: {e}")
        connections = build_run_connections(Path("/nonexistent"))  # all-default fallback

    return IndexEntry(
        run_id=run_id,
        run_dir=str(run_dir.relative_to(PROJECT_ROOT)),
        phase=phase,
        scenario_name=scenario_name,
        algorithm=algorithm,
        gnss_state=gnss_state,
        world_variant=world_variant,
        tag_source=tag_source,
        contract_status=contract_status,
        accepted=accepted,
        created_utc=created_utc,
        last_modified_utc=_iso(last_modified),
        key_metrics=key_metrics,
        artifacts=artifacts,
        connections=connections,
        comparisons=sorted(run_to_comparisons.get(run_id, [])),
        warnings=warnings,
    )


def build_comparison_entry(comp_dir: Path, run_to_comparisons: dict[str, list[str]]) -> ComparisonIndexEntry | None:
    manifest_path = comp_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    warnings: list[str] = []
    try:
        manifest = load_comparison_manifest(manifest_path)
    except Exception as e:
        return ComparisonIndexEntry(
            comparison_id=comp_dir.name,
            comparison_dir=str(comp_dir.relative_to(PROJECT_ROOT)),
            name=comp_dir.name,
            title=comp_dir.name,
            case_count=0,
            run_ids=[],
            has_report_md=(comp_dir / "report.md").is_file(),
            has_summary_csv=(comp_dir / "summary.csv").is_file(),
            warnings=[f"manifest.yaml failed to load: {e}"],
        )

    return ComparisonIndexEntry(
        comparison_id=comp_dir.name,
        comparison_dir=str(comp_dir.relative_to(PROJECT_ROOT)),
        name=manifest.name,
        title=manifest.title,
        case_count=len(manifest.cases),
        run_ids=[Path(c.run_dir).name for c in manifest.cases],
        has_report_md=(comp_dir / "report.md").is_file(),
        has_summary_csv=(comp_dir / "summary.csv").is_file(),
        warnings=warnings,
    )


def build_index() -> ExperimentsIndex:
    run_to_comparisons, run_to_tags = build_reverse_comparison_index()

    run_entries: list[IndexEntry] = []
    if RUNS_DIR.is_dir():
        for name in sorted(RUNS_DIR.iterdir()):
            if not name.is_dir():
                continue
            try:
                run_entries.append(build_run_entry(name, run_to_comparisons, run_to_tags))
            except Exception as e:
                # Absolute last resort - even the "never crash" wrapper crashed.
                # Still emit an entry rather than aborting the whole index.
                run_entries.append(
                    IndexEntry(
                        run_id=name.name,
                        run_dir=str(name.relative_to(PROJECT_ROOT)),
                        tag_source="unknown",
                        contract_status="incomplete",
                        last_modified_utc=_iso(datetime.now(timezone.utc)),
                        key_metrics={},
                        artifacts={},
                        connections=build_run_connections(Path("/nonexistent")),
                        comparisons=[],
                        warnings=[f"unhandled error building entry: {e}"],
                    )
                )

    comparison_entries: list[ComparisonIndexEntry] = []
    if COMPARISONS_DIR.is_dir():
        for name in sorted(COMPARISONS_DIR.iterdir()):
            if not name.is_dir():
                continue
            entry = build_comparison_entry(name, run_to_comparisons)
            if entry is not None:
                comparison_entries.append(entry)

    return ExperimentsIndex(
        schema_version=SCHEMA_VERSION,
        generated_utc=_iso(datetime.now(timezone.utc)),
        runs=run_entries,
        comparisons=comparison_entries,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=INDEX_PATH, help="Output path (default: experiments/index.json)"
    )
    args = parser.parse_args()

    index = build_index()

    counts: dict[str, int] = {}
    for entry in index.runs:
        counts[entry.contract_status] = counts.get(entry.contract_status, 0) + 1

    args.out.write_text(index.model_dump_json(indent=2) + "\n")

    print(f"Wrote {args.out} ({len(index.runs)} runs, {len(index.comparisons)} comparisons)")
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")
    warned = sum(1 for e in index.runs if e.warnings)
    if warned:
        print(f"  ({warned} runs have warnings - see index.json for details)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
