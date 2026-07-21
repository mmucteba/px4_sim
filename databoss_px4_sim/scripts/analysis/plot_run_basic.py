#!/usr/bin/env python3

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


def read_csv(run_dir: Path, name: str):
    path = run_dir / "extracted_csv" / name
    if not path.exists():
        print(f"Missing: {path}")
        return None
    return pd.read_csv(path)


def time_s(df):
    return (df["timestamp"] - df["timestamp"].iloc[0]) / 1_000_000.0


def save_plot(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def plot_xy(run_dir: Path):
    df = read_csv(run_dir, "vehicle_local_position.csv")
    if df is None:
        return

    plt.figure()
    plt.plot(df["x"], df["y"])
    plt.xlabel("Estimated X position [m]")
    plt.ylabel("Estimated Y position [m]")
    plt.title("PX4 EKF Local Position - XY")
    plt.axis("equal")
    plt.grid(True)
    save_plot(run_dir / "plots" / "ekf_xy_position.png")


def plot_altitude(run_dir: Path):
    df = read_csv(run_dir, "vehicle_local_position.csv")
    if df is None:
        return

    t = time_s(df)
    altitude = -df["z"]

    plt.figure()
    plt.plot(t, altitude)
    plt.xlabel("Time [s]")
    plt.ylabel("Estimated altitude [m]")
    plt.title("PX4 EKF Estimated Altitude")
    plt.grid(True)
    save_plot(run_dir / "plots" / "ekf_altitude.png")


def plot_gps_status(run_dir: Path):
    df = read_csv(run_dir, "vehicle_gps_position.csv")
    if df is None:
        return

    t = time_s(df)

    if "satellites_used" in df.columns:
        plt.figure()
        plt.plot(t, df["satellites_used"])
        plt.xlabel("Time [s]")
        plt.ylabel("Satellites used")
        plt.title("GPS Satellites Used")
        plt.grid(True)
        save_plot(run_dir / "plots" / "gps_satellites_used.png")

    if "fix_type" in df.columns:
        plt.figure()
        plt.plot(t, df["fix_type"])
        plt.xlabel("Time [s]")
        plt.ylabel("GPS fix type")
        plt.title("GPS Fix Type")
        plt.grid(True)
        save_plot(run_dir / "plots" / "gps_fix_type.png")

    if "eph" in df.columns:
        plt.figure()
        plt.plot(t, df["eph"])
        plt.xlabel("Time [s]")
        plt.ylabel("EPH [m]")
        plt.title("GPS Horizontal Accuracy Estimate")
        plt.grid(True)
        save_plot(run_dir / "plots" / "gps_eph.png")


def plot_estimator_accuracy(run_dir: Path):
    df = read_csv(run_dir, "estimator_status.csv")
    if df is None:
        return

    t = time_s(df)

    if "pos_horiz_accuracy" in df.columns:
        plt.figure()
        plt.plot(t, df["pos_horiz_accuracy"])
        plt.xlabel("Time [s]")
        plt.ylabel("Horizontal accuracy estimate [m]")
        plt.title("Estimator Horizontal Accuracy")
        plt.grid(True)
        save_plot(run_dir / "plots" / "estimator_horizontal_accuracy.png")

    if "pos_vert_accuracy" in df.columns:
        plt.figure()
        plt.plot(t, df["pos_vert_accuracy"])
        plt.xlabel("Time [s]")
        plt.ylabel("Vertical accuracy estimate [m]")
        plt.title("Estimator Vertical Accuracy")
        plt.grid(True)
        save_plot(run_dir / "plots" / "estimator_vertical_accuracy.png")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()

    plot_xy(run_dir)
    plot_altitude(run_dir)
    plot_gps_status(run_dir)
    plot_estimator_accuracy(run_dir)

    print(f"OK: plots written to {run_dir / 'plots'}")


if __name__ == "__main__":
    main()
