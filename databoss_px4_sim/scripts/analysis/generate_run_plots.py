#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = str(Path(tempfile.gettempdir()) / "databoss_mplconfig")

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


SCRIPT_VERSION = "2026-07-30.1"
MAX_POINTS = 4000
PLOT_DPI = 110
PLOT_FIGSIZE = (10, 5)



AIRBORNE_HEIGHT_M = 0.5


def _airborne_mask_for(runner, aid):
    """Boolean mask over `aid` rows for the airborne phase, or None.

    Reuses the SAME 0.5 m height threshold `flight.airborne_s` already uses, so
    the pack has ONE definition of airborne. Prefers ekf_vs_ground_truth_aligned,
    falling back to vehicle_local_position with the identical threshold.

    This mask exists because the unmasked numbers are actively misleading. On
    20260729_184510_example_01, over ALL samples the flow looked healthy
    (fused 88.4%, test_ratio p95 0.128, innovation_variance ~1e4-1e6). Masked to
    airborne it is fused 100%, test_ratio p95 0.0003, innovation_variance 0.09 -
    and the assigned observation noise turns out to be 26-52x the measured signal,
    i.e. the innovation gate cannot discriminate at all. The 1e4-1e6 variance was
    entirely the pre-takeoff ground phase. Reporting fused_pct unmasked would
    certify a run whose flow contributes nothing.
    """
    try:
        t_aid = runner.flow_aid_time(aid)
        if t_aid is None or not len(t_aid):
            return None, None
        aligned = runner.aligned()
        if aligned is not None and {"px4_t_rel_s", "px4_height_up"} <= set(aligned.columns):
            th = numeric(aligned["px4_t_rel_s"]).to_numpy(dtype=float)
            hh = numeric(aligned["px4_height_up"]).to_numpy(dtype=float)
            source = "ekf_vs_ground_truth_aligned.px4_height_up"
        else:
            lp = runner.local_position()
            if lp is None or "z" not in lp.columns:
                return None, None
            tl = runner.px4_time(lp)
            if tl is None:
                return None, None
            th = numeric(tl).to_numpy(dtype=float)
            hh = -numeric(lp["z"]).to_numpy(dtype=float)
            source = "vehicle_local_position.-z"
        good = np.isfinite(th) & np.isfinite(hh)
        if good.sum() < 2:
            return None, None
        h_at = np.interp(np.asarray(t_aid, dtype=float), th[good], hh[good])
        return (h_at > AIRBORNE_HEIGHT_M), source
    except Exception:
        return None, None


def _fusion_block(aid, mask):
    """fused/rejected/test_ratio plus the metrics that carry the MEANING."""
    import numpy as _np
    sub = aid.loc[mask] if mask is not None else aid
    n = int(len(sub))
    if n == 0:
        return {}
    out = {"samples": n}
    fused = numeric(sub["fused"]).fillna(0) > 0
    rejected = numeric(sub["innovation_rejected"]).fillna(0) > 0
    out["fused_pct"] = round_float(float(fused.sum() * 100.0 / n))
    out["innovation_rejected_pct"] = round_float(float(rejected.sum() * 100.0 / n))
    ratio = {}
    worst_p95 = None
    for axis, col in [("x", "test_ratio[0]"), ("y", "test_ratio[1]")]:
        if col not in sub.columns:
            continue
        v = numeric(sub[col]).dropna()
        v = v[_np.isfinite(v)]
        if len(v):
            p95 = float(v.quantile(0.95))
            ratio[axis] = {"median": round_float(float(v.median())),
                           "p95": round_float(p95), "max": round_float(float(v.max()))}
            worst_p95 = p95 if worst_p95 is None else max(worst_p95, p95)
    if ratio:
        out["test_ratio"] = ratio
        out["rejection_gate"] = 1.0

    # observation / innovation magnitudes and the assigned-noise ratio. A small
    # test_ratio proves nothing when the assigned sigma dwarfs the signal.
    obs_mean, inn_mean, ov_sigma = {}, {}, None
    for axis, ocol, icol in [("x", "observation[0]", "innovation[0]"), ("y", "observation[1]", "innovation[1]")]:
        if ocol in sub.columns:
            ov = numeric(sub[ocol]).abs().dropna()
            if len(ov):
                obs_mean[axis] = round_float(float(ov.mean()))
        if icol in sub.columns:
            iv = numeric(sub[icol]).abs().dropna()
            if len(iv):
                inn_mean[axis] = round_float(float(iv.mean()))
    if "observation_variance[0]" in sub.columns:
        ovar = numeric(sub["observation_variance[0]"]).dropna()
        if len(ovar) and float(ovar.mean()) > 0:
            ov_sigma = float(_np.sqrt(float(ovar.mean())))
            out["observation_variance_mean"] = round_float(float(ovar.mean()))
            out["ekf2_of_n_min_implied_sigma"] = round_float(ov_sigma)
    if obs_mean:
        out["observation_abs_mean"] = obs_mean
    if inn_mean:
        out["innovation_abs_mean"] = inn_mean
    if ov_sigma and obs_mean:
        out["assigned_sigma_over_observation"] = {
            a: round_float(ov_sigma / v) for a, v in obs_mean.items() if v}
    if obs_mean and inn_mean:
        out["innovation_over_observation"] = {
            a: round_float(inn_mean[a] / obs_mean[a]) for a in obs_mean
            if a in inn_mean and obs_mean[a]}

    # gate_discriminating: false when the gate has no power to reject, i.e. the
    # assigned noise dwarfs the signal. This is the field that stops "100% fused"
    # being read as success.
    ratios = out.get("assigned_sigma_over_observation") or {}
    if ratios:
        out["gate_discriminating"] = bool(max(ratios.values()) <= 3.0)
    return out


class RunPlotter:
    def __init__(self, run_dir: Path, force: bool) -> None:
        self.run_dir = run_dir
        self.force = force
        self.plots_dir = run_dir / "plots"
        self.run_name = run_dir.name
        self.csv_cache: dict[Path, pd.DataFrame | None] = {}
        self.csv_errors: dict[str, str] = {}
        self.sources: dict[str, str] = {}
        self.notes: list[str] = []
        self.px4_t0_s: float | None = None
        self.truth_t0_s: float | None = None
        self.gnss_cut_time_s: float | None = None
        self.gnss_cut_source = "not_found"
        self.innovation_columns_used: list[str] = []
        self.attitude_source: str | None = None
        self.previous_manifest: dict[str, Any] = {}

    def source_path(self, rel: str) -> Path:
        return self.run_dir / rel

    def display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.run_dir))
        except ValueError:
            return str(path)

    def find_sources(self) -> None:
        candidates = {
            "ekf_vs_ground_truth_aligned": self.source_path("ekf_vs_ground_truth_aligned.csv"),
            "vehicle_local_position": self.source_path("extracted_csv/vehicle_local_position.csv"),
            "vehicle_gps_position": self.source_path("extracted_csv/vehicle_gps_position.csv"),
            "vehicle_attitude": self.source_path("extracted_csv/vehicle_attitude.csv"),
            "estimator_innovations": self.source_path("extracted_csv/estimator_innovations.csv"),
            "estimator_status": self.source_path("extracted_csv/estimator_status.csv"),
            "estimator_aid_src_optical_flow": self.source_path("extracted_csv/estimator_aid_src_optical_flow.csv"),
            "estimator_optical_flow_vel": self.source_path("extracted_csv/estimator_optical_flow_vel.csv"),
            "vehicle_optical_flow": self.source_path("extracted_csv/vehicle_optical_flow.csv"),
            "estimator_status_flags": self.source_path("extracted_csv/estimator_status_flags.csv"),
            "sensor_baro": self.source_path("extracted_csv/sensor_baro.csv"),
            "flow_bridge_sent": self.source_path("flow_bridge/flow_bridge_sent.csv"),
            "rangefinder": self.source_path("flow_recording/rangefinder.csv"),
        }
        for key, path in candidates.items():
            if path.exists():
                self.sources[key] = self.display_path(path)

        truth = self.resolve_truth_csv()
        if truth is not None:
            self.sources["gazebo_truth"] = self.display_path(truth)

    def resolve_truth_csv(self) -> Path | None:
        summary = self.load_json(self.run_dir / "postprocess_summary.json")
        if isinstance(summary, dict):
            path_text = summary.get("truth", {}).get("truth_csv")
            if path_text:
                path = Path(path_text)
                rel = self.run_dir / "gazebo_truth" / path.name if path.is_absolute() else self.run_dir / path_text
                if rel.exists():
                    return rel
                if path.exists():
                    return path
        candidates = sorted((self.run_dir / "gazebo_truth").glob("gazebo_ground_truth_*.csv"))
        return candidates[0] if candidates else None

    def load_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            self.notes.append(f"could not read {path.name}: {exc}")
            return None

    def read_csv(self, rel: str | Path) -> pd.DataFrame | None:
        path = self.source_path(rel) if isinstance(rel, str) else rel
        if path in self.csv_cache:
            return self.csv_cache[path]
        if not path.exists():
            self.csv_cache[path] = None
            self.csv_errors[self.display_path(path)] = "file missing"
            return None
        try:
            df = pd.read_csv(path)
            if df.empty:
                raise ValueError("CSV is empty")
            if len(df.columns) == 0:
                raise ValueError("CSV has no columns")
            self.csv_cache[path] = df
            return df
        except Exception as exc:
            self.csv_cache[path] = None
            self.csv_errors[self.display_path(path)] = str(exc)
            return None

    def px4_time(self, df: pd.DataFrame) -> pd.Series | None:
        if "timestamp" not in df.columns:
            return None
        ts = numeric(df["timestamp"])
        if ts.dropna().empty:
            return None
        if self.px4_t0_s is None:
            self.px4_t0_s = float(ts.dropna().iloc[0]) * 1e-6
        return ts * 1e-6 - self.px4_t0_s

    def file_time(self, df: pd.DataFrame, candidates: list[str], units: str = "s") -> pd.Series | None:
        for col in candidates:
            if col not in df.columns:
                continue
            vals = numeric(df[col])
            if vals.dropna().empty:
                continue
            if units == "us":
                vals = vals * 1e-6
            return vals - float(vals.dropna().iloc[0])
        return None

    def aligned(self) -> pd.DataFrame | None:
        return self.read_csv("ekf_vs_ground_truth_aligned.csv")

    def local_position(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/vehicle_local_position.csv")

    def gps(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/vehicle_gps_position.csv")

    def flow_aid(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/estimator_aid_src_optical_flow.csv")

    def flow_velocity(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/estimator_optical_flow_vel.csv")

    def vehicle_optical_flow(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/vehicle_optical_flow.csv")

    def status_flags(self) -> pd.DataFrame | None:
        return self.read_csv("extracted_csv/estimator_status_flags.csv")

    def truth(self) -> pd.DataFrame | None:
        path = self.resolve_truth_csv()
        if path is None:
            return None
        return self.read_csv(path)

    def setup_time_bases(self) -> None:
        local = self.local_position()
        if local is not None and "timestamp" in local.columns:
            ts = numeric(local["timestamp"]).dropna()
            if not ts.empty:
                self.px4_t0_s = float(ts.iloc[0]) * 1e-6
        truth = self.truth()
        if truth is not None and "sim_time_s" in truth.columns:
            ts = numeric(truth["sim_time_s"]).dropna()
            if not ts.empty:
                self.truth_t0_s = float(ts.iloc[0])

    def derive_gnss_cut(self) -> None:
        for path in [self.run_dir / "postprocess_summary.json", *sorted(self.run_dir.glob("*_summary.json"))]:
            data = self.load_json(path)
            value = deep_find_key(data, {
                "effective_gnss_loss_t_rel_s",
                "effective_gnss_loss_time_s",
                "effective_gnss_loss_s",
                "gnss_cut_t_rel_s",
                "gnss_cut_time_s",
                "gnss_loss_t_rel_s",
                "gnss_loss_time_s",
            })
            if is_finite_number(value):
                self.gnss_cut_time_s = float(value)
                self.gnss_cut_source = str(path.relative_to(self.run_dir))
                return

        gps = self.gps()
        if gps is None:
            return
        t = self.px4_time(gps)
        if t is None:
            t = self.file_time(gps, ["timestamp"], "us")
        if t is None:
            return

        if "fix_type" in gps.columns:
            fix = numeric(gps["fix_type"])
            mask = fix < 3
            if mask.any():
                idx = mask[mask].index[0]
                value = float(t.loc[idx])
                if math.isfinite(value):
                    self.gnss_cut_time_s = value
                    self.gnss_cut_source = "extracted_csv/vehicle_gps_position.csv:fix_type<3"
                    return

        for col in ["eph", "epv"]:
            if col not in gps.columns:
                continue
            vals = numeric(gps[col])
            finite = vals[np.isfinite(vals)]
            if len(finite) < 5:
                continue
            baseline = finite.iloc[: min(20, len(finite))].median()
            threshold = max(float(baseline) * 5.0, float(baseline) + 10.0)
            mask = vals >= threshold
            if mask.any():
                idx = mask[mask].index[0]
                value = float(t.loc[idx])
                if math.isfinite(value):
                    self.gnss_cut_time_s = value
                    self.gnss_cut_source = f"extracted_csv/vehicle_gps_position.csv:{col}_jump"
                    return

    def maybe_existing(self, name: str) -> dict[str, Any] | None:
        out = self.plots_dir / name
        if out.exists() and not self.force:
            previous = self.previous_plot_record(name)
            print(f"SKIP {name}: existing file (use --force to regenerate)")
            return {
                "name": name,
                "generated": False,
                "reason_skipped": "already existed; use --force to regenerate",
                "inputs_used": previous.get("inputs_used", []),
            }
        return None

    def previous_plot_record(self, name: str) -> dict[str, Any]:
        for record in self.previous_manifest.get("plots", []):
            if isinstance(record, dict) and record.get("name") == name:
                return record
        return {}

    def save_plot(self, fig: plt.Figure, name: str) -> None:
        out = self.plots_dir / name
        fig.tight_layout()
        fig.savefig(
            out,
            dpi=PLOT_DPI,
            bbox_inches="tight",
            metadata={"Software": "DATABOSS generate_run_plots.py"},
            pil_kwargs={"optimize": True},
        )
        plt.close(fig)

    def plot_record(self, name: str, func) -> dict[str, Any]:
        existing = self.maybe_existing(name)
        if existing is not None:
            return existing
        try:
            result = func(name)
            if isinstance(result, dict):
                inputs = result.pop("inputs_used", [])
                extra = result
            else:
                inputs = result
                extra = {}
            print(f"GENERATED {name}")
            return {"name": name, "generated": True, "reason_skipped": "", "inputs_used": inputs, **extra}
        except SkipPlot as exc:
            print(f"SKIP {name}: {exc}")
            return {"name": name, "generated": False, "reason_skipped": str(exc), "inputs_used": exc.inputs}
        except Exception as exc:
            plt.close("all")
            print(f"SKIP {name}: exception: {exc}")
            return {
                "name": name,
                "generated": False,
                "reason_skipped": f"exception: {exc}",
                "inputs_used": [],
            }

    def draw_cut(self, ax: plt.Axes) -> None:
        if self.gnss_cut_time_s is None:
            return
        ax.axvline(self.gnss_cut_time_s, color="0.25", linestyle="--", linewidth=1.0, label="GNSS cut")

    def plot_trajectory_xy(self, name: str) -> list[str]:
        df = require_df(self.aligned(), "ekf_vs_ground_truth_aligned.csv")
        require_columns(df, ["px4_x_rel", "px4_y_rel", "gz_x_rel", "gz_y_rel"])
        p = downsample(df, ["px4_x_rel", "px4_y_rel", "gz_x_rel", "gz_y_rel"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["px4_x_rel"], p["px4_y_rel"], label="PX4 estimate")
        ax.plot(p["gz_x_rel"], p["gz_y_rel"], label="Gazebo truth")
        ax.scatter([p["px4_x_rel"].iloc[0]], [p["px4_y_rel"].iloc[0]], marker="o", s=40, label="start")
        if self.gnss_cut_time_s is not None and "px4_t_rel_s" in df.columns:
            idx = nearest_index(numeric(df["px4_t_rel_s"]), self.gnss_cut_time_s)
            if idx is not None:
                ax.scatter([df.loc[idx, "px4_x_rel"]], [df.loc[idx, "px4_y_rel"]], marker="x", s=70, label="GNSS cut")
        ax.set_title(f"Trajectory XY - {self.run_name}")
        ax.set_xlabel("North relative position (m)")
        ax.set_ylabel("East relative position (m)")
        ax.axis("equal")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return ["ekf_vs_ground_truth_aligned.csv"]

    def plot_altitude(self, name: str) -> list[str]:
        df = require_df(self.aligned(), "ekf_vs_ground_truth_aligned.csv")
        require_columns(df, ["px4_t_rel_s", "px4_height_up", "gz_height_up"])
        p = downsample(df, ["px4_t_rel_s", "px4_height_up", "gz_height_up"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["px4_t_rel_s"], p["px4_height_up"], label="PX4 estimate")
        ax.plot(p["px4_t_rel_s"], p["gz_height_up"], label="Gazebo truth")
        self.draw_cut(ax)
        ax.set_title(f"Altitude vs Time - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("Height up from start (m)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return ["ekf_vs_ground_truth_aligned.csv"]

    def plot_position_error(self, name: str) -> list[str]:
        df = require_df(self.aligned(), "ekf_vs_ground_truth_aligned.csv")
        require_columns(df, ["px4_t_rel_s", "horizontal_error_m", "abs_height_error_m"])
        p = downsample(df, ["px4_t_rel_s", "horizontal_error_m", "abs_height_error_m"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["px4_t_rel_s"], p["horizontal_error_m"], label="horizontal error")
        ax.plot(p["px4_t_rel_s"], p["abs_height_error_m"], label="absolute height error")
        self.draw_cut(ax)
        ax.set_title(f"Position Error - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("Error (m)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return ["ekf_vs_ground_truth_aligned.csv"]

    def plot_velocity(self, name: str) -> list[str]:
        df = require_df(self.local_position(), "extracted_csv/vehicle_local_position.csv")
        require_columns(df, ["timestamp", "vx", "vy", "vz"])
        t = self.px4_time(df)
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/vehicle_local_position.csv"])
        p = downsample(with_time(df, t), ["plot_time_s", "vx", "vy", "vz"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["plot_time_s"], p["vx"], label="vx")
        ax.plot(p["plot_time_s"], p["vy"], label="vy")
        ax.plot(p["plot_time_s"], p["vz"], label="vz")
        self.draw_cut(ax)
        ax.set_title(f"Velocity - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("Velocity (m/s)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return ["extracted_csv/vehicle_local_position.csv"]

    def plot_attitude(self, name: str) -> list[str]:
        attitude = self.read_csv("extracted_csv/vehicle_attitude.csv")
        if attitude is not None and all(c in attitude.columns for c in ["timestamp", "q[0]", "q[1]", "q[2]", "q[3]"]):
            t = self.px4_time(attitude)
            if t is None:
                raise SkipPlot("vehicle_attitude timestamp column has no numeric values", ["extracted_csv/vehicle_attitude.csv"])
            q = attitude[["q[0]", "q[1]", "q[2]", "q[3]"]].apply(numeric)
            source = "vehicle_attitude"
            inputs = ["extracted_csv/vehicle_attitude.csv"]
        else:
            truth = require_df(self.truth(), "gazebo_truth/gazebo_ground_truth_*.csv")
            require_columns(truth, ["sim_time_s", "qx", "qy", "qz", "qw"])
            t_raw = numeric(truth["sim_time_s"])
            t = t_raw - float(t_raw.dropna().iloc[0])
            q = pd.DataFrame({"q[0]": truth["qw"], "q[1]": truth["qx"], "q[2]": truth["qy"], "q[3]": truth["qz"]}).apply(numeric)
            source = "gazebo_truth"
            inputs = [self.sources.get("gazebo_truth", "gazebo_truth/gazebo_ground_truth_*.csv")]
        rpy = quaternion_to_euler_deg(q["q[0]"], q["q[1]"], q["q[2]"], q["q[3]"])
        data = pd.DataFrame({"plot_time_s": t, "roll_deg": rpy[0], "pitch_deg": rpy[1], "yaw_deg": rpy[2]})
        p = downsample(data, ["plot_time_s", "roll_deg", "pitch_deg", "yaw_deg"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["plot_time_s"], p["roll_deg"], label="roll")
        ax.plot(p["plot_time_s"], p["pitch_deg"], label="pitch")
        ax.plot(p["plot_time_s"], p["yaw_deg"], label="yaw")
        self.draw_cut(ax)
        ax.set_title(f"Attitude ({source}) - {self.run_name}")
        ax.set_xlabel("Relative time (s)")
        ax.set_ylabel("Euler angle (deg)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        self.attitude_source = source
        return inputs

    def plot_flow_quality(self, name: str) -> list[str]:
        df = require_df(self.read_csv("flow_bridge/flow_bridge_sent.csv"), "flow_bridge/flow_bridge_sent.csv")
        require_columns(df, ["t_frame_sim_s", "quality_sent", "n_matches"])
        df, t = flow_frame_data(df)
        p = downsample(with_time(df, t), ["plot_time_s", "quality_sent", "n_matches"])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax2 = ax.twinx()
        ax.plot(p["plot_time_s"], p["quality_sent"], color="tab:blue", label="quality_sent")
        ax2.plot(p["plot_time_s"], p["n_matches"], color="tab:orange", label="n_matches")
        if "sent" in df.columns:
            sent = numeric(df["sent"]).fillna(0) > 0
            unsent = with_time(df.loc[~sent], t.loc[~sent]) if (~sent).any() else pd.DataFrame()
            if not unsent.empty:
                u = downsample(unsent, ["plot_time_s"])
                ax.scatter(u["plot_time_s"], np.zeros(len(u)), color="tab:red", marker="|", s=30, label="unsent")
        self.draw_cut(ax)
        ax.set_title(f"Flow Quality - {self.run_name}")
        ax.set_xlabel("Flow frame relative time (s)")
        ax.set_ylabel("Optical flow quality (0-255)")
        ax2.set_ylabel("Matched features (count)")
        ax.grid(alpha=0.3)
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2)
        self.save_plot(fig, name)
        return ["flow_bridge/flow_bridge_sent.csv"]

    def plot_flow_vs_gyro(self, name: str) -> list[str]:
        df = require_df(self.read_csv("flow_bridge/flow_bridge_sent.csv"), "flow_bridge/flow_bridge_sent.csv")
        cols = ["t_frame_sim_s", "integrated_x_sent", "integrated_y_sent", "integrated_xgyro_sent", "integrated_ygyro_sent"]
        require_columns(df, cols)
        df, t = flow_frame_data(df)
        p = downsample(with_time(df, t), ["plot_time_s", *cols[1:]])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["plot_time_s"], p["integrated_x_sent"], label="flow x")
        ax.plot(p["plot_time_s"], p["integrated_y_sent"], label="flow y")
        ax.plot(p["plot_time_s"], p["integrated_xgyro_sent"], label="gyro x")
        ax.plot(p["plot_time_s"], p["integrated_ygyro_sent"], label="gyro y")
        self.draw_cut(ax)
        ax.set_title(f"Flow vs Gyro Integrals - {self.run_name}")
        ax.set_xlabel("Flow frame relative time (s)")
        ax.set_ylabel("Integrated angle (rad)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return ["flow_bridge/flow_bridge_sent.csv"]

    def plot_gps_health(self, name: str) -> list[str]:
        df = require_df(self.gps(), "extracted_csv/vehicle_gps_position.csv")
        cols = [c for c in ["eph", "epv", "hdop", "vdop"] if c in df.columns]
        if not cols:
            raise SkipPlot("none of eph, epv, hdop, vdop columns exist", ["extracted_csv/vehicle_gps_position.csv"])
        t = self.px4_time(df)
        if t is None:
            t = self.file_time(df, ["timestamp"], "us")
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/vehicle_gps_position.csv"])
        p = downsample(with_time(df, t), ["plot_time_s", *cols])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        for col in cols:
            ax.plot(p["plot_time_s"], p[col], label=col)
        self.draw_cut(ax)
        ax.set_title(f"GPS Health - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("GPS accuracy / dilution (m or unitless)")
        ax.grid(alpha=0.3)
        if len(cols) > 1:
            ax.legend()
        self.save_plot(fig, name)
        return ["extracted_csv/vehicle_gps_position.csv"]

    def plot_ekf_innovations(self, name: str) -> list[str]:
        df = require_df(self.read_csv("extracted_csv/estimator_innovations.csv"), "extracted_csv/estimator_innovations.csv")
        candidates = [
            "gps_hpos[0]",
            "gps_hpos[1]",
            "gps_vpos",
            "gps_hvel[0]",
            "gps_hvel[1]",
            "gps_vvel",
            "flow[0]",
            "flow[1]",
        ]
        cols = [c for c in candidates if c in df.columns][:6]
        if not cols:
            raise SkipPlot("no GPS/flow innovation columns found", ["extracted_csv/estimator_innovations.csv"])
        t = self.px4_time(df)
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/estimator_innovations.csv"])
        p = downsample(with_time(df, t), ["plot_time_s", *cols])
        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        for col in cols:
            ax.plot(p["plot_time_s"], p[col], label=col)
        self.draw_cut(ax)
        ax.set_title(f"EKF Innovations - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("Innovation (m or m/s)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        self.innovation_columns_used = cols
        return ["extracted_csv/estimator_innovations.csv"]

    def plot_height_sources(self, name: str) -> list[str]:
        series: list[tuple[str, pd.Series, pd.Series]] = []
        inputs: list[str] = []

        local = self.local_position()
        if local is not None and all(c in local.columns for c in ["timestamp", "z"]):
            t = self.px4_time(local)
            z = numeric(local["z"])
            if t is not None and not z.dropna().empty:
                series.append(("EKF height", t, -(z - float(z.dropna().iloc[0]))))
                inputs.append("extracted_csv/vehicle_local_position.csv")
            if t is not None and "dist_bottom" in local.columns:
                dist = numeric(local["dist_bottom"])
                finite = dist[np.isfinite(dist)]
                if not finite.empty and finite.max() > 0:
                    series.append(("EKF dist_bottom", t, dist))

        truth = self.truth()
        if truth is not None and all(c in truth.columns for c in ["sim_time_s", "z"]):
            t_raw = numeric(truth["sim_time_s"])
            z = numeric(truth["z"])
            if not t_raw.dropna().empty and not z.dropna().empty:
                t = t_raw - float(t_raw.dropna().iloc[0])
                series.append(("truth height", t, z - float(z.dropna().iloc[0])))
                inputs.append(self.sources.get("gazebo_truth", "gazebo_truth/gazebo_ground_truth_*.csv"))

        baro = self.read_csv("extracted_csv/sensor_baro.csv")
        if baro is not None and all(c in baro.columns for c in ["timestamp", "pressure"]):
            t = self.px4_time(baro)
            pressure = numeric(baro["pressure"])
            finite = pressure[np.isfinite(pressure)]
            if t is not None and not finite.empty and float(finite.iloc[0]) > 0:
                p0 = float(finite.iloc[0])
                baro_height = 44330.0 * (1.0 - np.power(pressure / p0, 0.1903))
                series.append(("baro rel height", t, baro_height))
                inputs.append("extracted_csv/sensor_baro.csv")

        rng = self.read_csv("flow_recording/rangefinder.csv")
        if rng is not None and all(c in rng.columns for c in ["t_sim_s", "range_m"]):
            t = numeric(rng["t_sim_s"])
            if self.truth_t0_s is not None:
                t = t - self.truth_t0_s
            else:
                t = t - float(t.dropna().iloc[0])
            range_m = numeric(rng["range_m"])
            if not range_m.dropna().empty:
                series.append(("rangefinder", t, range_m))
                inputs.append("flow_recording/rangefinder.csv")

        if len(series) < 2:
            raise SkipPlot("fewer than two height sources found", inputs)

        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        for label, t, values in series:
            data = pd.DataFrame({"plot_time_s": t, label: values})
            p = downsample(data, ["plot_time_s", label])
            ax.plot(p["plot_time_s"], p[label], label=label)
        self.draw_cut(ax)
        ax.set_title(f"Height Sources - {self.run_name}")
        ax.set_xlabel("Relative time (s)")
        ax.set_ylabel("Height / range (m)")
        ax.grid(alpha=0.3)
        ax.legend()
        self.save_plot(fig, name)
        return sorted(set(inputs))

    def flow_aid_time(self, df: pd.DataFrame) -> pd.Series:
        require_columns(df, ["timestamp"])
        t = self.px4_time(df)
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/estimator_aid_src_optical_flow.csv"])
        return t

    def cs_opt_flow_intervals(self) -> tuple[list[tuple[float, float]], list[str]]:
        flags = self.status_flags()
        if flags is None:
            return [], []
        if not all(c in flags.columns for c in ["timestamp", "cs_opt_flow"]):
            return [], ["extracted_csv/estimator_status_flags.csv"]
        t = self.px4_time(flags)
        if t is None:
            return [], ["extracted_csv/estimator_status_flags.csv"]
        data = pd.DataFrame({"t": t, "active": numeric(flags["cs_opt_flow"]).fillna(0) > 0}).dropna(subset=["t"])
        if len(data) < 2:
            return [], ["extracted_csv/estimator_status_flags.csv"]
        data = data.sort_values("t")
        intervals: list[tuple[float, float]] = []
        times = data["t"].to_numpy(dtype=float)
        active = data["active"].to_numpy(dtype=bool)
        for i in range(len(times) - 1):
            if active[i] and math.isfinite(times[i]) and math.isfinite(times[i + 1]) and times[i + 1] > times[i]:
                intervals.append((float(times[i]), float(times[i + 1])))
        return intervals, ["extracted_csv/estimator_status_flags.csv"]

    def shade_cs_opt_flow(self, ax: plt.Axes) -> list[str]:
        intervals, inputs = self.cs_opt_flow_intervals()
        labelled = False
        for start, end in intervals:
            ax.axvspan(start, end, color="tab:green", alpha=0.10, label="cs_opt_flow active" if not labelled else None)
            labelled = True
        return inputs

    def plot_flow_ekf_fusion(self, name: str) -> list[str]:
        df = require_df(self.flow_aid(), "extracted_csv/estimator_aid_src_optical_flow.csv")
        require_columns(df, ["timestamp", "fused", "innovation_rejected", "test_ratio[0]", "test_ratio[1]"])
        t = self.flow_aid_time(df)
        p = downsample(with_time(df, t), ["plot_time_s", "fused", "innovation_rejected", "test_ratio[0]", "test_ratio[1]"])
        fused = numeric(p["fused"]).fillna(0) > 0
        rejected = numeric(p["innovation_rejected"]).fillna(0) > 0

        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.fill_between(
            p["plot_time_s"],
            0,
            1,
            where=fused,
            step="post",
            color="tab:green",
            alpha=0.24,
            label="fused accepted",
        )
        if rejected.any():
            ax.scatter(
                p.loc[rejected, "plot_time_s"],
                np.full(int(rejected.sum()), 0.52),
                color="tab:red",
                marker="x",
                s=34,
                linewidths=1.2,
                label="innovation_rejected",
                zorder=5,
            )
        self.shade_cs_opt_flow(ax)
        self.draw_cut(ax)
        ax.set_ylim(-0.05, 1.05)
        ax.set_yticks([0, 1])
        ax.set_yticklabels(["not fused", "fused"])
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("EKF optical-flow aiding state")
        ax.grid(alpha=0.3)

        ax2 = ax.twinx()
        ax2.plot(p["plot_time_s"], p["test_ratio[0]"], color="tab:blue", linewidth=1.4, label="test_ratio x")
        ax2.plot(p["plot_time_s"], p["test_ratio[1]"], color="tab:orange", linewidth=1.4, label="test_ratio y")
        ax2.axhline(1.0, color="0.15", linestyle=":", linewidth=1.2, label="EKF rejection gate 1.0")
        ax2.set_ylabel("Innovation test ratio (unitless)")

        ax.set_title(f"Optical-Flow EKF Aiding Fusion - {self.run_name}")
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, loc="upper right", fontsize=8)
        self.save_plot(fig, name)
        return sorted(set(["extracted_csv/estimator_aid_src_optical_flow.csv", *self.cs_opt_flow_intervals()[1]]))

    def plot_flow_innovation(self, name: str) -> list[str]:
        df = require_df(self.flow_aid(), "extracted_csv/estimator_aid_src_optical_flow.csv")
        cols = ["timestamp", "innovation[0]", "innovation[1]", "innovation_variance[0]", "innovation_variance[1]"]
        require_columns(df, cols)
        t = self.flow_aid_time(df)
        data = with_time(df, t)
        p = downsample(data, ["plot_time_s", *cols[1:]])
        sig_x = np.sqrt(np.clip(numeric(p["innovation_variance[0]"]), 0, None))
        sig_y = np.sqrt(np.clip(numeric(p["innovation_variance[1]"]), 0, None))

        fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
        ax.plot(p["plot_time_s"], p["innovation[0]"], color="tab:blue", label="innovation x")
        ax.fill_between(p["plot_time_s"], -sig_x, sig_x, color="tab:blue", alpha=0.14, label="+/-1 sigma x")
        ax.plot(p["plot_time_s"], p["innovation[1]"], color="tab:orange", label="innovation y")
        ax.fill_between(p["plot_time_s"], -sig_y, sig_y, color="tab:orange", alpha=0.14, label="+/-1 sigma y")
        self.draw_cut(ax)
        ax.set_title(f"Optical-Flow EKF Innovations - {self.run_name}")
        ax.set_xlabel("PX4 relative time (s)")
        ax.set_ylabel("Innovation (rad/s)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
        self.save_plot(fig, name)
        return ["extracted_csv/estimator_aid_src_optical_flow.csv"]

    def flow_velocity_reference(self, flow_t: pd.Series) -> dict[str, Any]:
        flow_t = numeric(flow_t)
        finite_flow_t = flow_t[np.isfinite(flow_t)]
        if finite_flow_t.empty:
            raise SkipPlot("flow velocity timestamp column has no numeric values", ["extracted_csv/estimator_optical_flow_vel.csv"])

        aligned = self.aligned()
        truth = self.truth()
        if (
            aligned is not None
            and truth is not None
            and all(c in aligned.columns for c in ["px4_t_rel_s", "gz_x_rel", "gz_y_rel"])
            and all(c in truth.columns for c in ["sim_time_s", "qx", "qy", "qz", "qw"])
        ):
            adf = pd.DataFrame({
                "px4_t": numeric(aligned["px4_t_rel_s"]),
                "gz_x": numeric(aligned["gz_x_rel"]),
                "gz_y": numeric(aligned["gz_y_rel"]),
            })
            if "gz_t_rel_s" in aligned.columns:
                adf["gz_t"] = numeric(aligned["gz_t_rel_s"])
            else:
                adf["gz_t"] = adf["px4_t"]
            adf = adf.replace([np.inf, -np.inf], np.nan).dropna().sort_values("px4_t")
            if len(adf) >= 5 and float(adf["px4_t"].max() - adf["px4_t"].min()) > 0:
                t_truth = numeric(truth["sim_time_s"])
                if self.truth_t0_s is not None:
                    t_truth = t_truth - self.truth_t0_s
                else:
                    finite_truth_t = t_truth[np.isfinite(t_truth)]
                    if not finite_truth_t.empty:
                        t_truth = t_truth - float(finite_truth_t.iloc[0])
                q = pd.DataFrame({
                    "w": numeric(truth["qw"]),
                    "x": numeric(truth["qx"]),
                    "y": numeric(truth["qy"]),
                    "z": numeric(truth["qz"]),
                    "t": t_truth,
                }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("t")
                if len(q) >= 5:
                    _, _, yaw_deg = quaternion_to_euler_deg(q["w"], q["x"], q["y"], q["z"])
                    yaw = np.unwrap(np.radians(yaw_deg.to_numpy(dtype=float)))
                    yaw_on_aligned = np.interp(adf["gz_t"].to_numpy(dtype=float), q["t"].to_numpy(dtype=float), yaw)
                    px4_t = adf["px4_t"].to_numpy(dtype=float)
                    vx_world = np.gradient(adf["gz_x"].to_numpy(dtype=float), px4_t)
                    vy_world = np.gradient(adf["gz_y"].to_numpy(dtype=float), px4_t)
                    cos_yaw = np.cos(yaw_on_aligned)
                    sin_yaw = np.sin(yaw_on_aligned)
                    vx_body = cos_yaw * vx_world + sin_yaw * vy_world
                    vy_body = -sin_yaw * vx_world + cos_yaw * vy_world
                    return {
                        "x": pd.Series(np.interp(flow_t, px4_t, vx_body), index=flow_t.index),
                        "y": pd.Series(np.interp(flow_t, px4_t, vy_body), index=flow_t.index),
                        "source": "gazebo_truth",
                        "frame": "body frame; derived from ekf_vs_ground_truth_aligned.csv Gazebo position derivatives and Gazebo yaw",
                        "inputs_used": ["ekf_vs_ground_truth_aligned.csv", self.sources.get("gazebo_truth", "gazebo_truth/gazebo_ground_truth_*.csv")],
                    }

        gps = self.gps()
        attitude = self.read_csv("extracted_csv/vehicle_attitude.csv")
        if (
            gps is not None
            and attitude is not None
            and all(c in gps.columns for c in ["timestamp", "vel_n_m_s", "vel_e_m_s"])
            and all(c in attitude.columns for c in ["timestamp", "q[0]", "q[1]", "q[2]", "q[3]"])
        ):
            gps_t = self.px4_time(gps)
            att_t = self.px4_time(attitude)
            if gps_t is not None and att_t is not None:
                gps_data = pd.DataFrame({
                    "t": gps_t,
                    "vn": numeric(gps["vel_n_m_s"]),
                    "ve": numeric(gps["vel_e_m_s"]),
                }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("t")
                q = attitude[["q[0]", "q[1]", "q[2]", "q[3]"]].apply(numeric)
                _, _, yaw_deg = quaternion_to_euler_deg(q["q[0]"], q["q[1]"], q["q[2]"], q["q[3]"])
                att_data = pd.DataFrame({"t": att_t, "yaw": np.unwrap(np.radians(yaw_deg))}).replace([np.inf, -np.inf], np.nan).dropna().sort_values("t")
                if len(gps_data) >= 3 and len(att_data) >= 3:
                    yaw = np.interp(gps_data["t"].to_numpy(dtype=float), att_data["t"].to_numpy(dtype=float), att_data["yaw"].to_numpy(dtype=float))
                    cos_yaw = np.cos(yaw)
                    sin_yaw = np.sin(yaw)
                    vx_body = cos_yaw * gps_data["vn"].to_numpy(dtype=float) + sin_yaw * gps_data["ve"].to_numpy(dtype=float)
                    vy_body = -sin_yaw * gps_data["vn"].to_numpy(dtype=float) + cos_yaw * gps_data["ve"].to_numpy(dtype=float)
                    gps_time = gps_data["t"].to_numpy(dtype=float)
                    return {
                        "x": pd.Series(np.interp(flow_t, gps_time, vx_body), index=flow_t.index),
                        "y": pd.Series(np.interp(flow_t, gps_time, vy_body), index=flow_t.index),
                        "source": "vehicle_gps_position+vehicle_attitude",
                        "frame": "body frame; GPS N/E velocity rotated by vehicle_attitude yaw",
                        "inputs_used": ["extracted_csv/vehicle_gps_position.csv", "extracted_csv/vehicle_attitude.csv"],
                    }

        if gps is not None and all(c in gps.columns for c in ["timestamp", "vel_n_m_s", "vel_e_m_s"]):
            gps_t = self.px4_time(gps)
            if gps_t is not None:
                gps_data = pd.DataFrame({
                    "t": gps_t,
                    "speed": np.sqrt(numeric(gps["vel_n_m_s"]) ** 2 + numeric(gps["vel_e_m_s"]) ** 2),
                }).replace([np.inf, -np.inf], np.nan).dropna().sort_values("t")
                if len(gps_data) >= 3:
                    gps_time = gps_data["t"].to_numpy(dtype=float)
                    speed = pd.Series(np.interp(flow_t, gps_time, gps_data["speed"].to_numpy(dtype=float)), index=flow_t.index)
                    return {
                        "x": speed,
                        "y": speed,
                        "source": "vehicle_gps_position",
                        "frame": "horizontal speed magnitude only; not body-axis resolved",
                        "caveat": "GPS speed magnitude is plotted on both axes only as a magnitude reference, not as a body-frame axis comparison.",
                        "inputs_used": ["extracted_csv/vehicle_gps_position.csv"],
                    }

        raise SkipPlot("no Gazebo truth body velocity, GPS body velocity, or GPS speed magnitude reference derivable")

    def plot_flow_velocity_vs_reference(self, name: str) -> dict[str, Any]:
        df = require_df(self.flow_velocity(), "extracted_csv/estimator_optical_flow_vel.csv")
        require_columns(df, ["timestamp", "vel_body[0]", "vel_body[1]"])
        t = self.px4_time(df)
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/estimator_optical_flow_vel.csv"])
        ref = self.flow_velocity_reference(t)
        data = pd.DataFrame({
            "plot_time_s": t,
            "flow_vel_body_x": numeric(df["vel_body[0]"]),
            "flow_vel_body_y": numeric(df["vel_body[1]"]),
            "reference_x": ref["x"],
            "reference_y": ref["y"],
        })
        p = downsample(data, ["plot_time_s", "flow_vel_body_x", "flow_vel_body_y", "reference_x", "reference_y"])

        fig, axes = plt.subplots(2, 1, figsize=PLOT_FIGSIZE, sharex=True)
        axes[0].plot(p["plot_time_s"], p["flow_vel_body_x"], label="PX4 flow vel_body x")
        axes[0].plot(p["plot_time_s"], p["reference_x"], linestyle="--", label="reference x")
        axes[0].set_ylabel("Body x velocity (m/s)")
        axes[0].grid(alpha=0.3)
        axes[0].legend(fontsize=8)
        self.draw_cut(axes[0])

        axes[1].plot(p["plot_time_s"], p["flow_vel_body_y"], label="PX4 flow vel_body y")
        axes[1].plot(p["plot_time_s"], p["reference_y"], linestyle="--", label="reference y")
        axes[1].set_xlabel("PX4 relative time (s)")
        axes[1].set_ylabel("Body y velocity (m/s)")
        axes[1].grid(alpha=0.3)
        axes[1].legend(fontsize=8)
        self.draw_cut(axes[1])

        fig.suptitle(f"Optical-Flow Body Velocity vs Reference - {self.run_name}")
        self.save_plot(fig, name)
        return {
            "inputs_used": sorted(set(["extracted_csv/estimator_optical_flow_vel.csv", *ref.get("inputs_used", [])])),
            "reference_source": ref["source"],
            "reference_frame": ref["frame"],
            **({"reference_caveat": ref["caveat"]} if ref.get("caveat") else {}),
        }

    def plot_flow_input_quality(self, name: str) -> list[str]:
        df = require_df(self.vehicle_optical_flow(), "extracted_csv/vehicle_optical_flow.csv")
        cols = ["timestamp", "quality", "distance_m", "integration_timespan_us"]
        require_columns(df, cols)
        t = self.px4_time(df)
        if t is None:
            raise SkipPlot("timestamp column has no numeric values", ["extracted_csv/vehicle_optical_flow.csv"])
        p = downsample(with_time(df, t), cols[1:] + ["plot_time_s"])

        fig, axes = plt.subplots(3, 1, figsize=PLOT_FIGSIZE, sharex=True)
        axes[0].plot(p["plot_time_s"], p["quality"], color="tab:blue")
        axes[0].set_ylabel("Quality (0-255)")
        axes[1].plot(p["plot_time_s"], p["distance_m"], color="tab:green")
        axes[1].set_ylabel("Distance (m)")
        axes[2].plot(p["plot_time_s"], p["integration_timespan_us"], color="tab:orange")
        axes[2].set_ylabel("Integration span (us)")
        axes[2].set_xlabel("PX4 relative time (s)")
        for ax in axes:
            self.draw_cut(ax)
            ax.grid(alpha=0.3)
        fig.suptitle(f"PX4-Received Optical-Flow Input Quality - {self.run_name}")
        self.save_plot(fig, name)
        return ["extracted_csv/vehicle_optical_flow.csv"]

    def build_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "script_version": SCRIPT_VERSION,
        }
        flight: dict[str, Any] = {}
        accuracy: dict[str, Any] = {}
        gnss: dict[str, Any] = {}
        flow: dict[str, Any] = {}
        flow_fusion: dict[str, Any] = {}

        try:
            aligned = self.aligned()
            if aligned is not None:
                if "px4_t_rel_s" in aligned.columns:
                    t = numeric(aligned["px4_t_rel_s"]).dropna()
                    if len(t) >= 2:
                        flight["duration_s"] = round_float(float(t.iloc[-1] - t.iloc[0]))
                if "px4_height_up" in aligned.columns:
                    h = numeric(aligned["px4_height_up"]).dropna()
                    if not h.empty:
                        flight["max_height_up_m"] = round_float(float(h.max()))
                        airborne = aligned.loc[numeric(aligned["px4_height_up"]) > 0.5]
                        if "px4_t_rel_s" in airborne.columns and len(airborne) >= 2:
                            ta = numeric(airborne["px4_t_rel_s"]).dropna()
                            if len(ta) >= 2:
                                flight["airborne_s"] = round_float(float(ta.iloc[-1] - ta.iloc[0]))
                add_distribution(accuracy, "horizontal_error_m", aligned, "horizontal_error_m", ["mean", "median", "p95", "max"])
                add_distribution(accuracy, "abs_height_error_m", aligned, "abs_height_error_m", ["mean", "max"])
                if "horizontal_error_m" in aligned.columns:
                    vals = numeric(aligned["horizontal_error_m"]).dropna()
                    if not vals.empty:
                        accuracy["final_horizontal_error_m"] = round_float(float(vals.iloc[-1]))

                if self.gnss_cut_time_s is not None:
                    gnss["cut_time_s"] = round_float(self.gnss_cut_time_s)
                    gnss["cut_source"] = self.gnss_cut_source
                    if all(c in aligned.columns for c in ["px4_t_rel_s", "horizontal_error_m"]):
                        at = numeric(aligned["px4_t_rel_s"])
                        err = numeric(aligned["horizontal_error_m"])
                        before = err[at < self.gnss_cut_time_s].dropna()
                        after = err[at >= self.gnss_cut_time_s].dropna()
                        if not before.empty:
                            gnss["before_cut_horizontal_error_m"] = {
                                "mean": round_float(float(before.mean())),
                                "max": round_float(float(before.max())),
                            }
                        if not after.empty:
                            gnss["after_cut_horizontal_error_m"] = {
                                "mean": round_float(float(after.mean())),
                                "max": round_float(float(after.max())),
                            }
                        fit_df = pd.DataFrame({"t": at, "err": err}).dropna()
                        fit_df = fit_df[fit_df["t"] >= self.gnss_cut_time_s]
                        if len(fit_df) >= 2 and float(fit_df["t"].max() - fit_df["t"].min()) > 0:
                            slope, _ = np.polyfit(fit_df["t"], fit_df["err"], 1)
                            gnss["post_cut_drift_rate_m_s"] = round_float(float(slope))
        except Exception as exc:
            self.notes.append(f"stats accuracy/flight skipped in part: {exc}")

        try:
            local = self.local_position()
            if local is not None and all(c in local.columns for c in ["vx", "vy"]):
                vx = numeric(local["vx"])
                vy = numeric(local["vy"])
                speed = np.sqrt(vx * vx + vy * vy)
                speed = speed[np.isfinite(speed)]
                if len(speed):
                    flight["max_horizontal_speed_m_s"] = round_float(float(speed.max()))
        except Exception as exc:
            self.notes.append(f"stats local velocity skipped: {exc}")

        try:
            flow_df = self.read_csv("flow_bridge/flow_bridge_sent.csv")
            if flow_df is not None:
                flow["samples_total"] = int(len(flow_df))
                if "sent" in flow_df.columns:
                    sent = numeric(flow_df["sent"]).fillna(0) > 0
                elif "mavlink_sent" in flow_df.columns:
                    sent = numeric(flow_df["mavlink_sent"]).fillna(0) > 0
                else:
                    sent = pd.Series([True] * len(flow_df))
                samples_sent = int(sent.sum())
                flow["samples_sent"] = samples_sent
                if len(flow_df):
                    flow["send_rate_pct"] = round_float(samples_sent * 100.0 / len(flow_df))
                for key in ["quality_sent", "n_matches"]:
                    if key in flow_df.columns:
                        vals = numeric(flow_df.loc[sent, key] if len(sent) == len(flow_df) else flow_df[key]).dropna()
                        vals = vals[np.isfinite(vals)]
                        if len(vals):
                            if key == "quality_sent":
                                flow[key] = {"mean": round_float(float(vals.mean())), "median": round_float(float(vals.median()))}
                            else:
                                flow[key] = {"mean": round_float(float(vals.mean())), "min": round_float(float(vals.min()))}
                if "t_frame_sim_s" in flow_df.columns:
                    _, t_all = flow_frame_data(flow_df)
                    t = t_all.dropna()
                    if len(t) >= 2:
                        span = float(t.max() - t.min())
                        if span > 0:
                            flow["effective_hz"] = round_float(len(flow_df) / span)
        except Exception as exc:
            self.notes.append(f"stats flow skipped: {exc}")

        try:
            aid = self.flow_aid()
            if aid is not None:
                required = ["fused", "innovation_rejected", "test_ratio[0]", "test_ratio[1]"]
                if all(c in aid.columns for c in required):
                    samples = int(len(aid))
                    if samples > 0:
                        # AIRBORNE-ONLY is the headline. Unmasked numbers include the
                        # pre-takeoff ground phase and are misleading - see the
                        # docstring on _airborne_mask_for.
                        air_mask, air_source = _airborne_mask_for(self, aid)
                        primary = _fusion_block(aid, air_mask)
                        flow_fusion.update(primary)
                        if air_mask is not None:
                            flow_fusion["mask"] = {
                                "definition": f"height_up > {AIRBORNE_HEIGHT_M} m",
                                "source": air_source,
                                "airborne_samples": int(air_mask.sum()),
                                "total_samples": samples,
                            }
                            ground = _fusion_block(aid, ~air_mask)
                            if ground:
                                # Labelled, never silently dropped: the contrast with
                                # the airborne block is itself the diagnostic.
                                flow_fusion["ground_phase"] = ground
                        else:
                            flow_fusion["mask"] = {
                                "definition": "UNMASKED - airborne height unavailable",
                                "source": None,
                                "total_samples": samples,
                            }

                        if self.gnss_cut_time_s is not None and "timestamp" in aid.columns:
                            t = self.px4_time(aid)
                            if t is not None:
                                for label, mask in [
                                    ("before_gnss_cut", t < self.gnss_cut_time_s),
                                    ("after_gnss_cut", t >= self.gnss_cut_time_s),
                                ]:
                                    if bool(mask.any()):
                                        denom = int(mask.sum())
                                        flow_fusion[label] = {
                                            "samples": denom,
                                            "fused_pct": round_float(float(fused.loc[mask].sum() * 100.0 / denom)),
                                            "innovation_rejected_pct": round_float(float(rejected.loc[mask].sum() * 100.0 / denom)),
                                        }
                else:
                    missing = [c for c in required if c not in aid.columns]
                    self.notes.append(f"stats flow_fusion aid-source skipped: missing columns {', '.join(missing)}")

            flags = self.status_flags()
            if flags is not None and all(c in flags.columns for c in ["timestamp", "cs_opt_flow"]):
                t = self.px4_time(flags)
                if t is not None:
                    data = pd.DataFrame({"t": t, "active": numeric(flags["cs_opt_flow"]).fillna(0) > 0}).dropna(subset=["t"])
                    data = data.sort_values("t")
                    if len(data) >= 2:
                        times = data["t"].to_numpy(dtype=float)
                        active = data["active"].to_numpy(dtype=bool)
                        dt = np.diff(times)
                        valid = np.isfinite(dt) & (dt > 0)
                        if valid.any():
                            total_s = float(dt[valid].sum())
                            active_s = float(dt[valid & active[:-1]].sum())
                            if total_s > 0:
                                flow_fusion["cs_opt_flow_active_s"] = round_float(active_s)
                                flow_fusion["cs_opt_flow_active_pct"] = round_float(active_s * 100.0 / total_s)

            flow_vel = self.flow_velocity()
            if flow_vel is not None and all(c in flow_vel.columns for c in ["timestamp", "vel_body[0]", "vel_body[1]"]):
                vel_body: dict[str, Any] = {}
                for axis, col in [("x", "vel_body[0]"), ("y", "vel_body[1]")]:
                    vals = numeric(flow_vel[col]).dropna()
                    vals = vals[np.isfinite(vals)]
                    if len(vals):
                        vel_body[axis] = round_float(float(vals.abs().mean()))
                if vel_body:
                    flow_fusion["vel_body_abs_mean"] = vel_body

                t = self.px4_time(flow_vel)
                if t is not None:
                    try:
                        ref = self.flow_velocity_reference(t)
                        source_frame = f"{ref['source']}: {ref['frame']}"
                        flow_fusion["reference_source"] = source_frame
                        ref_abs: dict[str, Any] = {}
                        for axis in ["x", "y"]:
                            vals = numeric(ref[axis]).dropna()
                            vals = vals[np.isfinite(vals)]
                            if len(vals):
                                ref_abs[axis] = round_float(float(vals.abs().mean()))
                        if ref_abs:
                            flow_fusion["reference_abs_mean"] = ref_abs
                        if ref.get("caveat"):
                            flow_fusion["reference_caveat"] = ref["caveat"]
                    except Exception as exc:
                        self.notes.append(f"stats flow_fusion reference skipped: {exc}")
        except Exception as exc:
            self.notes.append(f"stats flow_fusion skipped: {exc}")

        if flight:
            stats["flight"] = flight
        if accuracy:
            stats["accuracy"] = accuracy
        if gnss:
            stats["gnss"] = gnss
        if flow:
            stats["flow"] = flow
        if flow_fusion:
            stats["flow_fusion"] = omit_empty(flow_fusion)
        if self.sources:
            stats["sources"] = self.sources
        if self.notes:
            stats["notes"] = self.notes
        return stats

    def write_stats_md(self, stats: dict[str, Any]) -> None:
        lines = ["# Run Stats", ""]
        for group in ["flight", "accuracy", "gnss", "flow", "flow_fusion"]:
            value = stats.get(group)
            if not isinstance(value, dict) or not value:
                continue
            lines.extend([f"## {group.title()}", "", "| Metric | Value |", "|---|---|"])
            for key, val in flatten(value):
                lines.append(f"| {key} | {format_md_value(val)} |")
            lines.append("")
        if "sources" in stats:
            lines.extend(["## Sources", "", "| Source | Path |", "|---|---|"])
            for key, val in stats["sources"].items():
                lines.append(f"| {key} | `{val}` |")
            lines.append("")
        (self.run_dir / "run_stats.md").write_text("\n".join(lines))

    def run(self) -> int:
        self.plots_dir.mkdir(parents=True, exist_ok=True)
        self.previous_manifest = self.load_json(self.plots_dir / "_manifest.json") or {}
        self.find_sources()
        self.setup_time_bases()
        self.derive_gnss_cut()

        records = [
            self.plot_record("01_trajectory_xy.png", self.plot_trajectory_xy),
            self.plot_record("02_altitude_vs_time.png", self.plot_altitude),
            self.plot_record("03_position_error.png", self.plot_position_error),
            self.plot_record("04_velocity.png", self.plot_velocity),
            self.plot_record("05_attitude.png", self.plot_attitude),
            self.plot_record("06_flow_quality.png", self.plot_flow_quality),
            self.plot_record("07_flow_vs_gyro.png", self.plot_flow_vs_gyro),
            self.plot_record("08_gps_health.png", self.plot_gps_health),
            self.plot_record("09_ekf_innovations.png", self.plot_ekf_innovations),
            self.plot_record("10_height_sources.png", self.plot_height_sources),
            self.plot_record("11_flow_ekf_fusion.png", self.plot_flow_ekf_fusion),
            self.plot_record("12_flow_innovation.png", self.plot_flow_innovation),
            self.plot_record("13_flow_velocity_vs_reference.png", self.plot_flow_velocity_vs_reference),
            self.plot_record("14_flow_input_quality.png", self.plot_flow_input_quality),
        ]

        stats = self.build_stats()
        try:
            (self.run_dir / "run_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
            print(f"WROTE {self.run_dir / 'run_stats.json'}")
        except Exception as exc:
            self.notes.append(f"could not write run_stats.json: {exc}")
            print(f"SKIP run_stats.json: {exc}")
        try:
            self.write_stats_md(stats)
            print(f"WROTE {self.run_dir / 'run_stats.md'}")
        except Exception as exc:
            self.notes.append(f"could not write run_stats.md: {exc}")
            print(f"SKIP run_stats.md: {exc}")

        manifest = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "script_version": SCRIPT_VERSION,
            "run_dir": str(self.run_dir),
            "gnss_cut_time_s": round_float(self.gnss_cut_time_s) if self.gnss_cut_time_s is not None else None,
            "gnss_cut_source": self.gnss_cut_source,
            "attitude_source": self.attitude_source or self.previous_manifest.get("attitude_source"),
            "innovation_columns_used": self.innovation_columns_used or self.previous_manifest.get("innovation_columns_used", []),
            "plots": records,
        }
        if self.csv_errors:
            manifest["csv_errors"] = self.csv_errors
        if self.notes:
            manifest["notes"] = self.notes
        try:
            (self.plots_dir / "_manifest.json").write_text(json.dumps(omit_empty(manifest), indent=2) + "\n")
            print(f"WROTE {self.plots_dir / '_manifest.json'}")
        except Exception as exc:
            print(f"SKIP plots/_manifest.json: {exc}")
        return 0


class SkipPlot(Exception):
    def __init__(self, reason: str, inputs: list[str] | None = None) -> None:
        super().__init__(reason)
        self.inputs = inputs or []


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def deep_find_key(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and is_finite_number(child):
                return child
            found = deep_find_key(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = deep_find_key(child, keys)
            if found is not None:
                return found
    return None


def require_df(df: pd.DataFrame | None, label: str) -> pd.DataFrame:
    if df is None:
        raise SkipPlot(f"{label} missing, empty, or unreadable", [label])
    return df


def require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise SkipPlot(f"missing columns: {', '.join(missing)}")


def nearest_index(series: pd.Series, value: float) -> Any | None:
    vals = numeric(series)
    vals = vals[np.isfinite(vals)]
    if vals.empty:
        return None
    return (vals - value).abs().idxmin()


def downsample(df: pd.DataFrame, columns: list[str], max_points: int = MAX_POINTS) -> pd.DataFrame:
    data = df.loc[:, [c for c in columns if c in df.columns]].copy()
    for col in data.columns:
        data[col] = numeric(data[col])
    data = data.dropna(how="all")
    if len(data) <= max_points:
        return data
    stride = max(1, math.ceil((len(data) - 2) / max_points))
    idx = [data.index[0], *data.index[1:-1:stride], data.index[-1]]
    return data.loc[sorted(set(idx))]


def with_time(df: pd.DataFrame, t: pd.Series) -> pd.DataFrame:
    out = df.copy()
    out["plot_time_s"] = t
    return out


def flow_frame_data(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    t = numeric(df["t_frame_sim_s"])
    finite = t[np.isfinite(t)]
    if len(finite) >= 5:
        q01 = float(finite.quantile(0.01))
        q99 = float(finite.quantile(0.99))
        span = q99 - q01
        if span > 0:
            keep = t.between(q01 - 3.0 * span, q99 + 3.0 * span)
            if keep.any():
                df = df.loc[keep].copy()
                t = t.loc[keep]
    finite = t[np.isfinite(t)]
    if finite.empty:
        raise SkipPlot("t_frame_sim_s column has no numeric values", ["flow_bridge/flow_bridge_sent.csv"])
    return df, t - float(finite.min())


def quaternion_to_euler_deg(w: pd.Series, x: pd.Series, y: pd.Series, z: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = np.arctan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (w * y - z * x)
    pitch = np.arcsin(np.clip(sinp, -1.0, 1.0))

    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = np.arctan2(siny_cosp, cosy_cosp)
    return np.degrees(roll), np.degrees(pitch), np.degrees(yaw)


def round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def add_distribution(out: dict[str, Any], name: str, df: pd.DataFrame, column: str, stats: list[str]) -> None:
    if column not in df.columns:
        return
    vals = numeric(df[column]).dropna()
    vals = vals[np.isfinite(vals)]
    if vals.empty:
        return
    item: dict[str, float | None] = {}
    if "mean" in stats:
        item["mean"] = round_float(float(vals.mean()))
    if "median" in stats:
        item["median"] = round_float(float(vals.median()))
    if "p95" in stats:
        item["p95"] = round_float(float(vals.quantile(0.95)))
    if "max" in stats:
        item["max"] = round_float(float(vals.max()))
    out[name] = omit_empty(item)


def flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for key, value in data.items():
        label = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.extend(flatten(value, label))
        else:
            rows.append((label, value))
    return rows


def format_md_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def omit_empty(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            cleaned = omit_empty(child)
            if cleaned is None or cleaned == "" or cleaned == {} or cleaned == []:
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        out = []
        for child in value:
            cleaned = omit_empty(child)
            if cleaned not in (None, "", {}, []):
                out.append(cleaned)
        return out
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate default DATABOSS plots and stats for a run.")
    parser.add_argument("run_dir", help="Run directory")
    parser.add_argument("--force", action="store_true", help="Regenerate plots even when PNGs already exist")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = Path.cwd() / run_dir
    if not run_dir.exists():
        print(f"ERROR: run_dir does not exist: {run_dir}")
        return 1
    try:
        return RunPlotter(run_dir, args.force).run()
    except Exception as exc:
        print(f"ERROR recorded but not fatal: {exc}")
        try:
            plots_dir = run_dir / "plots"
            plots_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "script_version": SCRIPT_VERSION,
                "run_dir": str(run_dir),
                "gnss_cut_source": "not_found",
                "plots": [],
                "fatal_error_recorded_nonfatal": str(exc),
            }
            (plots_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
