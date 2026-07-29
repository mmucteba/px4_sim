#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BATCHES_DIR = PROJECT_ROOT / "experiments" / "batches"

FAILSAFE_PATTERNS = [
    "Failsafe activated",
    "Failsafe",
    "failsafe",
    "Preflight Fail",
    "No connection to the GCS",
    "Battery unhealthy",
    "Landing at current position",
    "Entering Hold",
    "entering Hold",
]


def latest_batch() -> Path:
    batches = sorted(BATCHES_DIR.glob("*phase7b_gnss_cases"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not batches:
        raise FileNotFoundError("No phase7b_gnss_cases batch folder found.")
    return batches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", default=None)
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir).resolve() if args.batch_dir else latest_batch().resolve()
    summary_path = batch_dir / "batch_summary.json"

    if not summary_path.exists():
        print(f"ERROR: missing {summary_path}")
        return 1

    batch = json.loads(summary_path.read_text())
    rows = []

    for result in batch.get("results", []):
        run_dir_raw = result.get("run_dir")
        run_dir = Path(run_dir_raw) if run_dir_raw else None
        console = run_dir / "logs" / "px4_gazebo_console.log" if run_dir else None

        text = console.read_text(errors="ignore") if console and console.exists() else ""

        matched = []
        counts = {}

        for pattern in FAILSAFE_PATTERNS:
            count = text.count(pattern)
            counts[pattern] = count
            if count > 0:
                matched.append(pattern)

        rows.append({
            "case": result.get("name"),
            "accepted": result.get("accepted"),
            "run_dir": run_dir_raw,
            "console_exists": bool(console and console.exists()),
            "failsafe_related_seen": bool(matched),
            "matched_patterns": "; ".join(matched),
            "failsafe_activated_count": counts.get("Failsafe activated", 0),
            "preflight_fail_count": counts.get("Preflight Fail", 0),
            "no_gcs_count": counts.get("No connection to the GCS", 0),
            "battery_unhealthy_count": counts.get("Battery unhealthy", 0),
        })

    csv_path = batch_dir / "batch_failsafe_audit.csv"
    md_path = batch_dir / "batch_failsafe_audit.md"

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Batch Failsafe Audit",
        "",
        f"- Batch folder: `{batch_dir}`",
        f"- Cases audited: `{len(rows)}`",
        "",
        "| Case | Accepted | Failsafe-related seen | Failsafe activated count | Preflight fail count | No-GCS count | Battery unhealthy count |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for r in rows:
        lines.append(
            f"| `{r['case']}` | `{r['accepted']}` | `{r['failsafe_related_seen']}` | "
            f"`{r['failsafe_activated_count']}` | `{r['preflight_fail_count']}` | "
            f"`{r['no_gcs_count']}` | `{r['battery_unhealthy_count']}` |"
        )

    lines.append("")
    md_path.write_text("\n".join(lines))

    print("== Failsafe audit ==")
    print(f"batch_dir={batch_dir}")
    print(f"csv={csv_path}")
    print(f"md={md_path}")
    print(f"rows={len(rows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
