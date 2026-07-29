#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_metrics(path: Path, label: str) -> list[dict]:
    rows = []
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["batch_label"] = label
            rows.append(row)
    return rows


def as_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--default-batch", required=True)
    parser.add_argument("--delayed-batch", required=True)
    args = parser.parse_args()

    default_dir = Path(args.default_batch).resolve()
    delayed_dir = Path(args.delayed_batch).resolve()

    default_csv = default_dir / "batch_metrics.csv"
    delayed_csv = delayed_dir / "batch_metrics.csv"

    if not default_csv.exists():
        raise SystemExit(f"missing {default_csv}")
    if not delayed_csv.exists():
        raise SystemExit(f"missing {delayed_csv}")

    default_rows = load_metrics(default_csv, "default_failsafe")
    delayed_rows = load_metrics(delayed_csv, "delayed_observation")

    by_case = {}
    for row in default_rows + delayed_rows:
        by_case.setdefault(row["case"], {})[row["batch_label"]] = row

    out_dir = PROJECT_ROOT / "experiments" / "comparisons" / "phase7b_default_vs_delayed"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_csv = out_dir / "comparison.csv"
    out_md = out_dir / "comparison.md"

    fieldnames = [
        "case",
        "default_h_mean_m",
        "default_h_max_m",
        "default_3d_max_m",
        "delayed_h_mean_m",
        "delayed_h_max_m",
        "delayed_3d_max_m",
        "delta_h_max_m",
        "delta_3d_max_m",
        "default_run",
        "delayed_run",
    ]

    rows = []
    for case, groups in by_case.items():
        d = groups.get("default_failsafe", {})
        o = groups.get("delayed_observation", {})

        default_h_max = as_float(d.get("horizontal_max_m"))
        delayed_h_max = as_float(o.get("horizontal_max_m"))
        default_3d_max = as_float(d.get("error_3d_max_m"))
        delayed_3d_max = as_float(o.get("error_3d_max_m"))

        row = {
            "case": case,
            "default_h_mean_m": d.get("horizontal_mean_m"),
            "default_h_max_m": d.get("horizontal_max_m"),
            "default_3d_max_m": d.get("error_3d_max_m"),
            "delayed_h_mean_m": o.get("horizontal_mean_m"),
            "delayed_h_max_m": o.get("horizontal_max_m"),
            "delayed_3d_max_m": o.get("error_3d_max_m"),
            "delta_h_max_m": None if default_h_max is None or delayed_h_max is None else delayed_h_max - default_h_max,
            "delta_3d_max_m": None if default_3d_max is None or delayed_3d_max is None else delayed_3d_max - default_3d_max,
            "default_run": d.get("run_dir"),
            "delayed_run": o.get("run_dir"),
        }
        rows.append(row)

    with out_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    def fmt(v):
        if v in (None, ""):
            return ""
        try:
            return f"{float(v):.6f}"
        except Exception:
            return str(v)

    lines = [
        "# Phase 7B Comparison — Default Failsafe vs Delayed Observation",
        "",
        f"- Default/current failsafe batch: `{default_dir}`",
        f"- Delayed-observation batch: `{delayed_dir}`",
        "",
        "| Case | Default H max m | Delayed H max m | Δ H max m | Default 3D max m | Delayed 3D max m | Δ 3D max m |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for r in rows:
        lines.append(
            "| "
            + " | ".join([
                f"`{r['case']}`",
                fmt(r["default_h_max_m"]),
                fmt(r["delayed_h_max_m"]),
                fmt(r["delta_h_max_m"]),
                fmt(r["default_3d_max_m"]),
                fmt(r["delayed_3d_max_m"]),
                fmt(r["delta_3d_max_m"]),
            ])
            + " |"
        )

    lines += [
        "",
        "## Interpretation",
        "",
        "Default-failsafe cases show PX4 protective behavior after GNSS loss.",
        "",
        "Delayed-observation cases expose drift over a longer observation window without immediate failsafe intervention.",
        "",
    ]

    out_md.write_text("\n".join(lines))

    print("== Phase 7B comparison ==")
    print(f"csv={out_csv}")
    print(f"md={out_md}")
    print(f"rows={len(rows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
