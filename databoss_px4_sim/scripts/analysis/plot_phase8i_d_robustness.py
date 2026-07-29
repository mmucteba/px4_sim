#!/usr/bin/env python3
"""Phase 8I Case D robustness plots (2026-07-15).

Visualizes the duration-limited marginal stability: one bounded realization
(170829, 35 s window) vs four that diverge over the full 50 s outage. Reads
each run's EKF estimate (vehicle_local_position) from the ULog referenced to
the GNSS-loss instant; the position controller tracks the EKF estimate, so
divergence of the estimate is the physical divergence.

Colorblind-safe: bounded = cool blue, diverged group = warm red ramp.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pyulog import ULog

PROJECT = Path(__file__).resolve().parents[2]
RUNS = PROJECT / "experiments/runs"
OUT = PROJECT / "experiments/comparisons/phase8i_flat_phototex_abcd_final_20260715/plots"

# (run glob, label, reject_over_fused, is_bounded)
SERIES = [
    ("20260714_170829_phase8i_d_loss*", "170829  (35 s window)", 0.326, True),
    ("20260715_113204_phase8i_d_loss*", "113204  batch (50 s)", 1.29, False),
    ("20260715_121400_phase8i_d_varirun*", "121400  vari-1 (50 s)", 1.42, False),
    ("20260715_122221_phase8i_d_varirun*", "122221  vari-2 (50 s)", 2.58, False),
    ("20260715_123107_phase8i_d_varirun*", "123107  vari-3 (50 s)", 1.38, False),
]
BOUNDED_C = "#2166ac"                                   # cool blue
DIVERGED_C = ["#a50f15", "#de2d26", "#fb6a4a", "#fc9272"]  # warm red ramp


def load(glob: str):
    d = sorted(RUNS.glob(glob))
    if not d:
        return None
    u = ULog(str(d[0] / "logs" / "flight.ulg"))
    vlp = u.get_dataset("vehicle_local_position").data
    t0 = float(vlp["timestamp"][0])
    t = (np.asarray(vlp["timestamp"], float) - t0) / 1e6
    x = np.asarray(vlp["x"], float); y = np.asarray(vlp["y"], float); z = np.asarray(vlp["z"], float)
    loss = None
    for tt, k, v in getattr(u, "changed_parameters", []):
        if k == "SIM_GPS_USED" and float(v) == 0:
            loss = tt / 1e6
    loss = loss if loss is not None else 22.0
    return t - loss, np.sqrt(x ** 2 + y ** 2), -z


def style(ax):
    ax.grid(True, which="both", alpha=0.25)
    ax.axvline(0.0, color="#777777", lw=0.9, alpha=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def series_style(i, bounded):
    if bounded:
        return dict(color=BOUNDED_C, lw=2.4, zorder=5)
    return dict(color=DIVERGED_C[(i - 1) % len(DIVERGED_C)], lw=1.6, alpha=0.9, zorder=3)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = [(lbl, rof, bnd, load(g)) for g, lbl, rof, bnd in SERIES]
    data = [(lbl, rof, bnd, d) for lbl, rof, bnd, d in data if d is not None]

    # ---- Fig 1: horizontal drift vs time since GNSS loss (log y) ----
    fig, ax1 = plt.subplots(figsize=(9.0, 5.0))
    for i, (lbl, _rof, bnd, (t, h, _alt)) in enumerate(data):
        m = t >= -3
        peak = float(h[m].max())
        ax1.plot(t[m], np.clip(h[m], 0.3, None), label=f"{lbl}   peak {peak:.0f} m",
                 **series_style(i, bnd))
    ax1.axhline(2.5, color="#111111", lw=0.9, ls=":", alpha=0.6)
    ax1.text(-2.8, 2.7, "2.5 m hold target", fontsize=8.5, color="#555")
    ax1.set_yscale("log")
    ax1.set_ylim(1.2, 550)
    ax1.set_xlim(-3, 41)
    ax1.set_title("Phase 8I Case D marginal stability: horizontal drift vs GNSS-loss time\n"
                  "1 bounded (blue, 35 s window) vs 4 diverged (red, full 50 s outage)",
                  fontsize=11.5)
    ax1.set_xlabel("time since GNSS loss (s)")
    ax1.set_ylabel("EKF horizontal distance from origin (m)")
    style(ax1)
    ax1.legend(fontsize=8.5, loc="upper left", framealpha=0.92)
    fig.tight_layout()
    fig.savefig(OUT / "d_robustness_drift.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ---- Fig 2: reject/fused across realizations with the strict gate ----
    fig, ax = plt.subplots(figsize=(8.5, 4.4))
    labels = [lbl.split()[0] for lbl, _, _, _ in data]
    vals = [rof for _, rof, _, _ in data]
    cols = [BOUNDED_C if bnd else DIVERGED_C[1] for _, _, bnd, _ in data]
    bars = ax.bar(labels, vals, color=cols, width=0.62, zorder=3)
    for b, v, (_, _, bnd, _) in zip(bars, vals, data):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.03, f"{v:.2f}",
                ha="center", va="bottom", fontsize=9, color="#222")
    ax.axhline(0.10, color="#111111", lw=1.2, ls="--", alpha=0.8)
    ax.text(len(labels) - 0.5, 0.13, "strict green gate  reject/fused < 0.10",
            ha="right", fontsize=8.5, color="#333")
    ax.set_ylabel("optical-flow reject / fused")
    ax.set_title("Case D optical-flow rejection ratio by realization\n"
                 "(blue = bounded 35 s; red = diverged 50 s)", fontsize=11)
    ax.grid(True, axis="y", alpha=0.25)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "d_reject_ratio_by_run.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    print("wrote:")
    for p in ("d_robustness_drift.png", "d_reject_ratio_by_run.png"):
        print(f"  {OUT / p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
