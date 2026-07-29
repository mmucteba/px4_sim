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


SCRIPT_VERSION = "2026-07-29.1"
MAX_POINTS = 4000
PLOT_DPI = 110
PLOT_FIGSIZE = (10, 5)


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
                "generated": True,
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
            inputs = func(name)
            print(f"GENERATED {name}")
            return {"name": name, "generated": True, "reason_skipped": "", "inputs_used": inputs}
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

    def build_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "script_version": SCRIPT_VERSION,
        }
        flight: dict[str, Any] = {}
        accuracy: dict[str, Any] = {}
        gnss: dict[str, Any] = {}
        flow: dict[str, Any] = {}

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

        if flight:
            stats["flight"] = flight
        if accuracy:
            stats["accuracy"] = accuracy
        if gnss:
            stats["gnss"] = gnss
        if flow:
            stats["flow"] = flow
        if self.sources:
            stats["sources"] = self.sources
        if self.notes:
            stats["notes"] = self.notes
        return stats

    def write_stats_md(self, stats: dict[str, Any]) -> None:
        lines = ["# Run Stats", ""]
        for group in ["flight", "accuracy", "gnss", "flow"]:
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
