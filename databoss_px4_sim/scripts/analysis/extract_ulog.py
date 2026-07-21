#!/usr/bin/env python3

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from pyulog import ULog
except Exception as exc:
    print(f"ERROR: pyulog is not available: {exc}", file=sys.stderr)
    print("Install with: python -m pip install pyulog", file=sys.stderr)
    sys.exit(2)


TARGET_TOPICS = [
    "vehicle_local_position",
    "vehicle_gps_position",
    "vehicle_attitude",
    "sensor_accel",
    "sensor_gyro",
    "vehicle_imu",
    "sensor_baro",
    "vehicle_magnetometer",
    "estimator_status",
    "estimator_innovations",
]


def safe_name(name: str) -> str:
    return name.replace("/", "_").replace(" ", "_")


def to_python_value(value):
    try:
        if hasattr(value, "item"):
            return value.item()
    except Exception:
        pass
    return value


def finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def dataset_filename(dataset) -> str:
    name = safe_name(dataset.name)
    multi_id = getattr(dataset, "multi_id", 0)
    if multi_id:
        return f"{name}__instance_{multi_id}.csv"
    return f"{name}.csv"


def export_dataset(dataset, out_dir: Path) -> dict:
    data = dataset.data
    if not data:
        return {
            "topic": dataset.name,
            "multi_id": getattr(dataset, "multi_id", 0),
            "rows": 0,
            "columns": [],
            "csv": None,
        }

    columns = list(data.keys())
    if "timestamp" in columns:
        columns = ["timestamp"] + sorted([c for c in columns if c != "timestamp"])
    else:
        columns = sorted(columns)

    lengths = []
    for col in columns:
        try:
            lengths.append(len(data[col]))
        except TypeError:
            pass

    rows = min(lengths) if lengths else 0
    csv_path = out_dir / dataset_filename(dataset)

    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for i in range(rows):
            row = []
            for col in columns:
                row.append(to_python_value(data[col][i]))
            writer.writerow(row)

    return {
        "topic": dataset.name,
        "multi_id": getattr(dataset, "multi_id", 0),
        "rows": rows,
        "columns": columns,
        "csv": str(csv_path),
    }


def get_datasets_by_name(ulog: ULog, topic: str):
    return [d for d in ulog.data_list if d.name == topic]


def first_dataset(ulog: ULog, topic: str):
    datasets = get_datasets_by_name(ulog, topic)
    return datasets[0] if datasets else None


def local_position_metrics(ulog: ULog) -> dict:
    ds = first_dataset(ulog, "vehicle_local_position")
    if ds is None:
        return {"available": False}

    data = ds.data
    required = ["timestamp", "x", "y", "z"]
    if not all(k in data for k in required):
        return {"available": False, "reason": "missing timestamp/x/y/z"}

    t = data["timestamp"]
    x = data["x"]
    y = data["y"]
    z = data["z"]

    valid_indices = []
    for i in range(min(len(t), len(x), len(y), len(z))):
        if finite_number(x[i]) and finite_number(y[i]) and finite_number(z[i]):
            valid_indices.append(i)

    if not valid_indices:
        return {"available": False, "reason": "no finite xyz samples"}

    i0 = valid_indices[0]
    x0 = float(x[i0])
    y0 = float(y[i0])
    z0 = float(z[i0])

    horizontal = []
    z_values = []

    for i in valid_indices:
        dx = float(x[i]) - x0
        dy = float(y[i]) - y0
        horizontal.append(math.sqrt(dx * dx + dy * dy))
        z_values.append(float(z[i]))

    duration_s = (float(t[valid_indices[-1]]) - float(t[valid_indices[0]])) / 1_000_000.0

    return {
        "available": True,
        "samples": len(valid_indices),
        "duration_s": duration_s,
        "start_xyz_m": [x0, y0, z0],
        "end_xyz_m": [float(x[valid_indices[-1]]), float(y[valid_indices[-1]]), float(z[valid_indices[-1]])],
        "max_horizontal_movement_from_start_m": max(horizontal),
        "mean_horizontal_movement_from_start_m": sum(horizontal) / len(horizontal),
        "min_z_m": min(z_values),
        "max_z_m": max(z_values),
    }


def gps_metrics(ulog: ULog) -> dict:
    ds = first_dataset(ulog, "vehicle_gps_position")
    if ds is None:
        return {"available": False}

    data = ds.data
    rows = len(data["timestamp"]) if "timestamp" in data else 0

    result = {
        "available": rows > 0,
        "samples": rows,
    }

    for key in ["fix_type", "satellites_used", "eph", "epv", "vel_m_s"]:
        if key in data and rows > 0:
            values = [to_python_value(v) for v in data[key]]
            try:
                result[f"{key}_min"] = float(min(values))
                result[f"{key}_max"] = float(max(values))
            except Exception:
                pass

    return result


def estimator_metrics(ulog: ULog) -> dict:
    ds = first_dataset(ulog, "estimator_status")
    if ds is None:
        return {"available": False}

    data = ds.data
    rows = len(data["timestamp"]) if "timestamp" in data else 0

    result = {
        "available": rows > 0,
        "samples": rows,
        "fields": sorted(list(data.keys())),
    }

    for key in ["gps_check_fail_flags", "control_mode_flags", "filter_fault_flags", "innovation_check_flags"]:
        if key in data and rows > 0:
            values = [int(to_python_value(v)) for v in data[key]]
            result[f"{key}_unique"] = sorted(list(set(values)))[:50]

    return result


def write_markdown_summary(path: Path, summary: dict):
    lines = []
    lines.append("# ULog Extraction Summary")
    lines.append("")
    lines.append(f"Created UTC: {summary['created_at_utc']}")
    lines.append(f"ULog: `{summary['ulog']}`")
    lines.append("")
    lines.append("## Exported topics")
    lines.append("")
    lines.append("| Topic | Instance | Rows | CSV |")
    lines.append("|---|---:|---:|---|")
    for item in summary["exports"]:
        csv_name = Path(item["csv"]).name if item["csv"] else ""
        lines.append(f"| {item['topic']} | {item['multi_id']} | {item['rows']} | {csv_name} |")

    lines.append("")
    lines.append("## Missing requested topics")
    lines.append("")
    if summary["missing_topics"]:
        for topic in summary["missing_topics"]:
            lines.append(f"- {topic}")
    else:
        lines.append("None")

    lines.append("")
    lines.append("## Basic metrics")
    lines.append("")
    lines.append("### Local position")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary["metrics"]["local_position"], indent=2))
    lines.append("```")
    lines.append("")
    lines.append("### GPS")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary["metrics"]["gps"], indent=2))
    lines.append("```")
    lines.append("")
    lines.append("### Estimator")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary["metrics"]["estimator"], indent=2))
    lines.append("```")
    lines.append("")

    path.write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="Experiment run directory")
    parser.add_argument("--topics", nargs="*", default=TARGET_TOPICS)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    ulog_path = run_dir / "logs" / "flight.ulg"
    out_dir = run_dir / "extracted_csv"
    plots_dir = run_dir / "plots"

    if not ulog_path.exists():
        print(f"ERROR: ULog not found: {ulog_path}", file=sys.stderr)
        sys.exit(1)

    out_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    ulog = ULog(str(ulog_path))

    exports = []
    missing_topics = []

    available_names = sorted(set(d.name for d in ulog.data_list))

    for topic in args.topics:
        datasets = get_datasets_by_name(ulog, topic)
        if not datasets:
            missing_topics.append(topic)
            continue

        for dataset in datasets:
            exports.append(export_dataset(dataset, out_dir))

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "ulog": str(ulog_path),
        "available_topic_count": len(available_names),
        "available_topics": available_names,
        "requested_topics": args.topics,
        "missing_topics": missing_topics,
        "exports": exports,
        "metrics": {
            "local_position": local_position_metrics(ulog),
            "gps": gps_metrics(ulog),
            "estimator": estimator_metrics(ulog),
        },
    }

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    write_markdown_summary(run_dir / "summary.md", summary)

    print(f"OK: extracted {len(exports)} dataset(s)")
    print(f"Run dir: {run_dir}")
    print(f"CSV dir: {out_dir}")
    print(f"Summary JSON: {run_dir / 'summary.json'}")
    print(f"Summary MD: {run_dir / 'summary.md'}")

    if missing_topics:
        print("Missing topics:")
        for topic in missing_topics:
            print(f"  - {topic}")


if __name__ == "__main__":
    main()
