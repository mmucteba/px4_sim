#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

try:
    from pyulog import ULog
except ImportError:
    print("ERROR: pyulog is missing. Activate venv or install pyulog.", file=sys.stderr)
    sys.exit(2)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "experiments" / "runs"

POSE_NAME_RE = re.compile(r'name:\s*"([^"]+)"')
SEC_RE = re.compile(r"sec:\s*([0-9]+)")
NSEC_RE = re.compile(r"nsec:\s*([0-9]+)")
FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
X_RE = re.compile(rf"\bx:\s*({FLOAT_RE})")
Y_RE = re.compile(rf"\by:\s*({FLOAT_RE})")
Z_RE = re.compile(rf"\bz:\s*({FLOAT_RE})")
W_RE = re.compile(rf"\bw:\s*({FLOAT_RE})")

TARGET_DATASETS = [
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
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def latest_run() -> Path:
    runs = sorted(RUNS_DIR.glob("*pxh_takeoff_land_truth"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        raise FileNotFoundError("No *pxh_takeoff_land_truth run found.")
    return runs[0]


def read_block_value(regex: re.Pattern, text: str, default: float = 0.0) -> float:
    m = regex.search(text)
    if not m:
        return default
    return float(m.group(1))


def parse_gazebo_truth(raw_path: Path, csv_path: Path, model_name: str = "x500_0") -> dict:
    text = raw_path.read_text(errors="ignore")
    messages = text.split("header {")
    rows = []

    for msg in messages:
        if not msg.strip():
            continue

        sec_m = SEC_RE.search(msg)
        nsec_m = NSEC_RE.search(msg)
        if not sec_m:
            continue

        sec = int(sec_m.group(1))
        nsec = int(nsec_m.group(1)) if nsec_m else 0
        sim_time_s = sec + nsec * 1e-9

        pose_chunks = msg.split("pose {")
        for pose in pose_chunks[1:]:
            name_m = POSE_NAME_RE.search(pose)
            if not name_m:
                continue

            name = name_m.group(1)
            if name != model_name:
                continue

            pos_start = pose.find("position {")
            ori_start = pose.find("orientation {")
            if pos_start < 0:
                continue

            pos_text = pose[pos_start:ori_start if ori_start > pos_start else len(pose)]
            ori_text = pose[ori_start:] if ori_start > 0 else ""

            rows.append({
                "sim_sec": sec,
                "sim_nsec": nsec,
                "sim_time_s": f"{sim_time_s:.9f}",
                "name": name,
                "x": read_block_value(X_RE, pos_text),
                "y": read_block_value(Y_RE, pos_text),
                "z": read_block_value(Z_RE, pos_text),
                "qx": read_block_value(X_RE, ori_text),
                "qy": read_block_value(Y_RE, ori_text),
                "qz": read_block_value(Z_RE, ori_text),
                "qw": read_block_value(W_RE, ori_text, 1.0),
            })
            break

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sim_sec", "sim_nsec", "sim_time_s", "name", "x", "y", "z", "qx", "qy", "qz", "qw"],
        )
        writer.writeheader()
        writer.writerows(rows)

    duration_s = float(rows[-1]["sim_time_s"]) - float(rows[0]["sim_time_s"]) if rows else 0.0

    return {
        "truth_csv": str(csv_path),
        "truth_model_name": model_name,
        "truth_rows": len(rows),
        "truth_duration_s": duration_s,
        "truth_first_time_s": float(rows[0]["sim_time_s"]) if rows else None,
        "truth_last_time_s": float(rows[-1]["sim_time_s"]) if rows else None,
    }


def write_dataset_csv(dataset, out_dir: Path) -> Path:
    name = dataset.name
    multi_id = getattr(dataset, "multi_id", 0)
    file_name = safe_name(name if multi_id == 0 else f"{name}_{multi_id}") + ".csv"
    out_path = out_dir / file_name

    data = dataset.data
    fields = list(data.keys())
    if not fields:
        out_path.write_text("")
        return out_path

    n = len(next(iter(data.values())))

    with out_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for i in range(n):
            row = []
            for field in fields:
                value = data[field][i]
                try:
                    value = value.item()
                except AttributeError:
                    pass
                row.append(value)
            writer.writerow(row)

    return out_path


def extract_ulog_direct(run_dir: Path) -> dict:
    ulog_path = run_dir / "logs" / "flight.ulg"
    out_dir = run_dir / "extracted_csv"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not ulog_path.exists():
        return {"ulog_exists": False, "extract_ok": False, "error": "missing logs/flight.ulg"}

    ulog = ULog(str(ulog_path))

    written = []
    available = []

    for dataset in ulog.data_list:
        available.append(dataset.name)
        if dataset.name in TARGET_DATASETS:
            out_path = write_dataset_csv(dataset, out_dir)
            written.append(str(out_path.relative_to(run_dir)))

    available_unique = sorted(set(available))
    written_unique = sorted(set(written))

    return {
        "ulog_exists": True,
        "extract_ok": len(written_unique) > 0,
        "extract_method": "direct_pyulog",
        "csv_count": len(written_unique),
        "csv_files": written_unique,
        "available_dataset_count": len(available_unique),
        "available_datasets_sample": available_unique[:80],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--model-name", default=None)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve() if args.run_dir else latest_run().resolve()
    if not run_dir.exists():
        print(f"ERROR: run dir not found: {run_dir}", file=sys.stderr)
        return 1

    raw_truth = run_dir / "gazebo_truth" / "gazebo_ground_truth_raw.txt"
    model_name = args.model_name
    status_path = run_dir / "logs" / "pxh_takeoff_land_truth_status.json"
    if model_name is None and status_path.exists():
        try:
            status = json.loads(status_path.read_text())
            model_name = status.get("gazebo_model_name")
        except Exception:
            model_name = None
    if not model_name:
        model_name = "x500_0"

    truth_csv = run_dir / "gazebo_truth" / f"gazebo_ground_truth_{safe_name(model_name)}.csv"

    if not raw_truth.exists():
        print(f"ERROR: missing Gazebo truth raw file: {raw_truth}", file=sys.stderr)
        return 1

    truth_summary = parse_gazebo_truth(raw_truth, truth_csv, model_name=model_name)
    ulog_summary = extract_ulog_direct(run_dir)

    accepted = truth_summary["truth_rows"] > 0 and ulog_summary.get("extract_ok", False)

    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "accepted": accepted,
        "truth": truth_summary,
        "ulog": ulog_summary,
    }

    (run_dir / "postprocess_summary.json").write_text(json.dumps(summary, indent=2))

    md = [
        "# Postprocess Summary",
        "",
        f"- Run: `{run_dir}`",
        f"- Gazebo truth CSV: `{truth_summary['truth_csv']}`",
        f"- Gazebo truth model name: `{truth_summary['truth_model_name']}`",
        f"- Gazebo truth rows: `{truth_summary['truth_rows']}`",
        f"- Gazebo truth duration_s: `{truth_summary['truth_duration_s']:.3f}`",
        f"- ULog extraction OK: `{ulog_summary.get('extract_ok', False)}`",
        f"- ULog extraction method: `{ulog_summary.get('extract_method')}`",
        f"- Extracted CSV count: `{ulog_summary.get('csv_count', 0)}`",
        "",
        "## Extracted CSV files",
        "",
    ]

    for path in ulog_summary.get("csv_files", []):
        md.append(f"- `{path}`")

    md += [
        "",
        "## Result",
        "",
        "Accepted." if accepted else "Rejected. Inspect postprocess_summary.json.",
        "",
    ]

    (run_dir / "postprocess_summary.md").write_text("\n".join(md))

    print("== Postprocess result ==")
    print(f"run_dir={run_dir}")
    print(f"truth_rows={truth_summary['truth_rows']}")
    print(f"truth_duration_s={truth_summary['truth_duration_s']:.3f}")
    print(f"ulog_extract_ok={ulog_summary.get('extract_ok', False)}")
    print(f"csv_count={ulog_summary.get('csv_count', 0)}")
    print(f"accepted={accepted}")

    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
