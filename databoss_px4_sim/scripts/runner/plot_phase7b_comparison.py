#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", newline="") as f:
        return list(csv.DictReader(f))


def as_float(v):
    if v in (None, ""):
        return None
    return float(v)


def short_case_name(name: str) -> str:
    mapping = {
        "baseline_gnss_on_hover_25s": "GNSS ON\n25s",
        "gnss_loss_after_takeoff_5s_post_25s": "Loss 5s\nPost 25s",
        "gnss_loss_after_takeoff_15s_post_25s": "Loss 15s\nPost 25s",
        "gnss_loss_after_takeoff_5s_post_45s": "Loss 5s\nPost 45s",
    }
    return mapping.get(name, name.replace("_", "\n"))


def plot_grouped_bar(rows: list[dict], default_col: str, delayed_col: str, title: str, ylabel: str, out_path: Path) -> None:
    labels = [short_case_name(r["case"]) for r in rows]
    default_vals = [as_float(r[default_col]) for r in rows]
    delayed_vals = [as_float(r[delayed_col]) for r in rows]

    x = list(range(len(rows)))
    width = 0.35

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.bar([i - width / 2 for i in x], default_vals, width, label="Default failsafe")
    ax.bar([i + width / 2 for i in x], delayed_vals, width, label="Delayed observation")

    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--comparison-csv",
        default="experiments/comparisons/phase7b_default_vs_delayed/comparison.csv",
    )
    args = parser.parse_args()

    comparison_csv = (PROJECT_ROOT / args.comparison_csv).resolve()
    if not comparison_csv.exists():
        raise SystemExit(f"missing comparison CSV: {comparison_csv}")

    out_dir = comparison_csv.parent / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = read_rows(comparison_csv)

    plots = [
        (
            "phase7b_horizontal_max_default_vs_delayed.png",
            "default_h_max_m",
            "delayed_h_max_m",
            "Phase 7B Horizontal Max Error",
            "Horizontal max error (m)",
        ),
        (
            "phase7b_3d_max_default_vs_delayed.png",
            "default_3d_max_m",
            "delayed_3d_max_m",
            "Phase 7B 3D Max Error",
            "3D max error (m)",
        ),
        (
            "phase7b_horizontal_mean_default_vs_delayed.png",
            "default_h_mean_m",
            "delayed_h_mean_m",
            "Phase 7B Horizontal Mean Error",
            "Horizontal mean error (m)",
        ),
    ]

    written = []

    for filename, default_col, delayed_col, title, ylabel in plots:
        out_path = out_dir / filename
        plot_grouped_bar(rows, default_col, delayed_col, title, ylabel, out_path)
        written.append(out_path)

    report = comparison_csv.parent / "phase7c_plot_report.md"
    lines = [
        "# Phase 7C Plot Report",
        "",
        f"- Comparison CSV: `{comparison_csv}`",
        f"- Plot folder: `{out_dir}`",
        "",
        "## Plots",
        "",
    ]

    for path in written:
        lines.append(f"- `{path}`")

    lines += [
        "",
        "## Interpretation",
        "",
        "Default-failsafe plots show PX4 protective behavior after GNSS loss.",
        "",
        "Delayed-observation plots expose drift without immediate failsafe intervention.",
        "",
        "The largest delayed-observation drift is expected in longer post-loss windows.",
        "",
    ]

    report.write_text("\n".join(lines))

    print("== Phase 7C plots ==")
    print(f"comparison_csv={comparison_csv}")
    print(f"plot_dir={out_dir}")
    print(f"report={report}")
    for path in written:
        print(f"plot={path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
