#!/usr/bin/env python3
"""Render and explain the Phase 11 three-way comparison plots."""

from __future__ import annotations

import argparse
import html
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
COMPARISON_DIR = ROOT / "experiments/comparisons/20260720_phase11_three_way_flow_comparison"
PLOTS_DIR = COMPARISON_DIR / "plots"
DEFAULT_OUT_DIR = Path("/tmp/phase11_three_way_flow_comparison_plot_walkthrough")


@dataclass(frozen=True)
class PlotNote:
    filename: str
    title: str
    proves: str
    read: str
    conclusion: str
    caveat: str


PLOTS = [
    PlotNote(
        "route_ekf_vs_gazebo_truth_overlay.png",
        "Route Overlay: PX4 EKF vs Gazebo Truth",
        "Navigation proof against physical ground truth.",
        "Each case overlays PX4 local-position route against Gazebo truth after frame/time alignment. Small separation means the estimator/controller stayed physically consistent with the simulated vehicle.",
        "LK and SIFT are tightly bounded. Stock 122327 is also bounded, but with larger final route disagreement than the bridge cases.",
        "This plot does not prove the sensors were valid; it only scores the final physical path. Use GNSS, flow, and range plots to prove why the path stayed bounded.",
    ),
    PlotNote(
        "route_ekf_vs_gazebo_truth_panels.png",
        "Route Panels: Per-Case Route Shape",
        "Per-system route behavior without overlay clutter.",
        "Each panel isolates one system, showing whether the vehicle followed the intended slow +Y/East route and whether PX4 and truth separate over time.",
        "All three complete a roughly straight slow translation. LK/SIFT are more directly comparable to each other because they share vehicle, camera, rangefinder, route, and timing.",
        "Stock uses a different vehicle and sensor implementation, so its panel is a native-stack comparator, not a one-variable estimator-only comparison.",
    ),
    PlotNote(
        "horizontal_error_vs_gazebo_truth.png",
        "Horizontal Error vs Gazebo Truth",
        "Main physical horizontal drift score.",
        "The y-axis is horizontal separation between PX4 local estimate and Gazebo truth. Lower and flatter is better; late growth means estimator/physical path disagreement accumulates.",
        "LK max horizontal error is about 1.09 m, SIFT about 1.21 m, stock about 1.35 m. Stock is bounded but not best on the valid GNSS-denied run.",
        "Do not confuse bounded error with acceptance. Stock still fails rangefinder validation.",
    ),
    PlotNote(
        "height_error_vs_gazebo_truth.png",
        "Height Error vs Gazebo Truth",
        "Vertical estimator accuracy against physical height.",
        "This compares PX4 height-up to Gazebo height-up. Large sustained error means EKF height or range/height aiding is disagreeing with truth.",
        "LK/SIFT stay under about 0.39 m max height error. Stock reaches about 0.63 m, consistent with the weaker stock rangefinder behavior.",
        "Height error is affected by estimator height references and rangefinder validity, so read this with the distance-sensor plot.",
    ),
    PlotNote(
        "height_px4_vs_gazebo_truth.png",
        "PX4 Height vs Gazebo Height",
        "Direct vertical trajectory comparison.",
        "This shows whether PX4 and Gazebo agree on climb, hover height, and any late vertical drift.",
        "The three runs reach about 2.5 m. Stock has a larger mismatch envelope than LK/SIFT.",
        "This is not a raw rangefinder plot; it is EKF local height versus physical truth.",
    ),
    PlotNote(
        "gazebo_truth_route_progress.png",
        "Gazebo Truth Route Progress",
        "Physical route actually flown, independent of EKF scoring.",
        "This uses Gazebo truth only. It tells us how far the vehicle physically moved along the intended route.",
        "All three move roughly 10-12 m physically. Stock moved farthest in this comparison window because its aligned duration is longer.",
        "Different aligned durations and skip-landing handling affect total path length; compare shape and boundedness more than exact endpoint alone.",
    ),
    PlotNote(
        "gnss_fix_satellites_over_time.png",
        "GNSS Fix Type And Satellites",
        "Observed GNSS-loss proof.",
        "The important event is fix type and satellites dropping from healthy values to zero. This is the proof that `SIM_GPS_USED 0` actually took effect.",
        "LK, SIFT, and stock 122327 all show real GPS loss. This fixes the earlier stock 115202 problem where the command was sent but ULog GPS stayed healthy.",
        "Timing labels depend on the chosen origin: ULog start, PX4 takeoff_time, or height-threshold takeoff.",
    ),
    PlotNote(
        "gnss_eph_epv_over_time.png",
        "GNSS Accuracy: EPH/EPV",
        "GPS health degradation after loss.",
        "EPH/EPV should jump from healthy values to large/unhealthy values when simulated GPS is removed.",
        "All three valid plotted cases show EPH/EPV degradation to about 100 m after loss.",
        "This plot proves GNSS became unhealthy, not that optical flow alone was perfect.",
    ),
    PlotNote(
        "optical_flow_fusion_fraction_1s.png",
        "Optical-Flow Fusion Fraction Per Second",
        "When EKF actually fused optical-flow aid.",
        "Each 1 s bin shows what fraction of optical-flow aid-source samples were fused. High means EKF was using the flow measurement.",
        "LK is near-continuous fusion, SIFT fuses strongly with fewer samples, and stock fuses substantially but less continuously.",
        "Fusion does not prove correct sign or physical accuracy by itself; it must agree with route/error plots.",
    ),
    PlotNote(
        "optical_flow_aid_sample_rate_1s.png",
        "Optical-Flow Aid Sample Rate",
        "Effective EKF aid-source frequency.",
        "This is the frequency of estimator optical-flow aid samples in 1 s bins, not just camera frame rate.",
        "LK commanded 40 Hz but effective sensor/aid rate is around 28-30 Hz because the camera source is 30 Hz. SIFT is around 14 Hz. Stock is native 50 Hz.",
        "A configured bridge rate above the physical camera rate does not create independent measurements.",
    ),
    PlotNote(
        "optical_flow_test_ratio_1s.png",
        "Optical-Flow Innovation Test Ratio",
        "EKF consistency check for flow measurements.",
        "Lower test ratio means the measurement innovations are comfortably inside EKF gates. Spikes suggest strain or pending rejection.",
        "All three current plotted cases have low optical-flow test ratios and zero optical-flow innovation-rejected counts.",
        "This is EKF internal consistency, not standalone camera-feature quality.",
    ),
    PlotNote(
        "optical_flow_quality_over_time.png",
        "Optical-Flow Quality Over Time",
        "Frontend measurement quality and dropouts.",
        "Quality is the PX4 optical-flow quality value. Zero means invalid/no useful flow for that sample.",
        "LK and SIFT quality is mostly high after rescaling. Stock quality is lower on average and has more zero-quality samples, but still fuses enough to stay bounded.",
        "Quality scales are frontend-specific; compare trends and dropouts more than raw algorithm meaning.",
    ),
    PlotNote(
        "ekf_control_status_flags.png",
        "EKF Control Status Flags",
        "Which aiding modes EKF says are active.",
        "The flags show active optical flow, GNSS position/velocity, range height, GPS height, and dead reckoning over the run.",
        "After GNSS loss, optical flow and range/GPS-height modes explain how EKF remains controlled. Stock has weaker range-height activity because its rangefinder is unreliable.",
        "Status flags are sampled coarsely; use aid-source counts/rates for exact fusion-frequency details.",
    ),
    PlotNote(
        "distance_sensor_current_distance.png",
        "Distance Sensor Current Distance",
        "Rangefinder validity and dropout evidence.",
        "This is the raw PX4 `distance_sensor.current_distance` stream. Finite stable values near hover height are good; `inf`, gaps, or implausible values are bad.",
        "LK/SIFT range is finite and stable. Stock 122327 has many non-finite samples and finite values drifting up toward 3.5 m while height is near 2.5 m; this is why stock validation is false.",
        "This plot is the main reason the stock run is not an accepted clean benchmark despite bounded route error.",
    ),
    PlotNote(
        "summary_metric_bars.png",
        "Summary Metric Bars",
        "Compact comparison of top-level metrics.",
        "This summarizes route error, height error, fusion counts/fractions, and other scalar metrics.",
        "The bars show LK/SIFT are not inferior to stock on the genuinely GNSS-denied comparison. Stock is bounded but rangefinder-rejected.",
        "Bar charts compress timelines. Use the individual plots to see when failures happen.",
    ),
]


def copy_plots(out_dir: Path) -> Path:
    copied = out_dir / "plots"
    copied.mkdir(parents=True, exist_ok=True)
    for note in PLOTS:
        src = PLOTS_DIR / note.filename
        if not src.exists():
            raise FileNotFoundError(src)
        shutil.copy2(src, copied / note.filename)
    return copied


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def make_contact_sheet(out_dir: Path, plot_dir: Path) -> Path:
    thumb_w = 560
    thumb_h = 330
    pad = 24
    title_h = 54
    cols = 2
    rows = (len(PLOTS) + cols - 1) // cols
    sheet_w = cols * thumb_w + (cols + 1) * pad
    sheet_h = rows * (thumb_h + title_h) + (rows + 1) * pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = load_font(24)
    small = load_font(18)

    for idx, note in enumerate(PLOTS):
        col = idx % cols
        row = idx // cols
        x = pad + col * (thumb_w + pad)
        y = pad + row * (thumb_h + title_h + pad)

        draw.text((x, y), f"{idx + 1}. {note.title}", fill=(20, 25, 30), font=font)
        img = Image.open(plot_dir / note.filename).convert("RGB")
        img.thumbnail((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        frame = Image.new("RGB", (thumb_w, thumb_h), (245, 247, 250))
        ix = (thumb_w - img.width) // 2
        iy = (thumb_h - img.height) // 2
        frame.paste(img, (ix, iy))
        sheet.paste(frame, (x, y + title_h))
        draw.rectangle((x, y + title_h, x + thumb_w, y + title_h + thumb_h), outline=(180, 185, 190), width=1)
        draw.text((x + 10, y + title_h + thumb_h - 28), note.filename, fill=(80, 85, 90), font=small)

    out = out_dir / "phase11_plot_contact_sheet.png"
    sheet.save(out)
    return out


def write_markdown(out_dir: Path, plot_dir: Path, contact_sheet: Path) -> Path:
    lines: list[str] = []
    lines.append("# Phase 11 Plot Walkthrough")
    lines.append("")
    lines.append("This report renders the Phase 11 three-way comparison plots and explains what each plot proves, what it does not prove, and how to use it in the final story.")
    lines.append("")
    lines.append("## Executive Reading")
    lines.append("")
    lines.append("- LK and SIFT are valid GNSS-denied bridge runs: GPS actually drops, optical flow fuses, and route error stays bounded against Gazebo truth.")
    lines.append("- Stock `122327` is finally genuinely GNSS-denied and stock optical flow fuses, but it is not accepted because the stock rangefinder stream is unreliable.")
    lines.append("- The most important plots are the route overlay, GNSS fix/satellites, optical-flow fusion fraction, distance sensor, and horizontal/height error plots.")
    lines.append("")
    lines.append("## Contact Sheet")
    lines.append("")
    lines.append(f"![Phase 11 plot contact sheet]({contact_sheet})")
    lines.append("")

    for idx, note in enumerate(PLOTS, start=1):
        image_path = plot_dir / note.filename
        lines.append(f"## {idx}. {note.title}")
        lines.append("")
        lines.append(f"![{note.title}]({image_path})")
        lines.append("")
        lines.append(f"**What it proves:** {note.proves}")
        lines.append("")
        lines.append(f"**How to read it:** {note.read}")
        lines.append("")
        lines.append(f"**Conclusion:** {note.conclusion}")
        lines.append("")
        lines.append(f"**Caveat:** {note.caveat}")
        lines.append("")

    lines.append("## Final Plot Story")
    lines.append("")
    lines.append("1. Start with `route_ekf_vs_gazebo_truth_overlay.png`: all three systems are physically bounded, with LK/SIFT slightly cleaner than stock in the current valid GNSS-denied comparison.")
    lines.append("2. Prove the test condition with `gnss_fix_satellites_over_time.png` and `gnss_eph_epv_over_time.png`: GPS actually becomes unhealthy in all three current plotted cases.")
    lines.append("3. Prove EKF optical-flow use with `optical_flow_fusion_fraction_1s.png` and `optical_flow_aid_sample_rate_1s.png`: LK/SIFT bridge flow and stock native flow are all being fed and fused.")
    lines.append("4. Explain why stock is still rejected with `distance_sensor_current_distance.png`: stock range has many non-finite and high-biased samples, unlike LK/SIFT.")
    lines.append("5. Close with horizontal/height error plots and the summary bars: the valid story is bounded GNSS-denied navigation for LK/SIFT, bounded-but-range-rejected diagnostic evidence for stock.")
    lines.append("")

    out = out_dir / "phase11_plot_walkthrough.md"
    out.write_text("\n".join(lines) + "\n")
    return out


def write_html(out_dir: Path, plot_dir: Path, contact_sheet: Path) -> Path:
    sections: list[str] = []
    sections.append("<!doctype html>")
    sections.append("<html lang=\"en\">")
    sections.append("<head>")
    sections.append("<meta charset=\"utf-8\">")
    sections.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    sections.append("<title>Phase 11 Plot Walkthrough</title>")
    sections.append(
        "<style>"
        "body{font-family:Arial,sans-serif;margin:32px;line-height:1.45;color:#17202a;background:#fff}"
        "h1{font-size:32px;margin-bottom:8px} h2{margin-top:36px;border-top:1px solid #d8dde3;padding-top:24px}"
        ".lead{max-width:980px}.plot{max-width:1120px;width:100%;border:1px solid #d8dde3;background:#f7f9fb}"
        ".card{max-width:1120px;margin-bottom:34px}.label{font-weight:700}.note{max-width:1020px}"
        "li{margin:6px 0} code{background:#eef1f4;padding:2px 4px;border-radius:4px}"
        "</style>"
    )
    sections.append("</head><body>")
    sections.append("<h1>Phase 11 Plot Walkthrough</h1>")
    sections.append(
        "<p class=\"lead\">Rendered Phase 11 three-way comparison plots with the engineering interpretation for each figure.</p>"
    )
    sections.append("<h2>Executive Reading</h2>")
    sections.append("<ul>")
    for item in [
        "LK and SIFT are valid GNSS-denied bridge runs: GPS actually drops, optical flow fuses, and route error stays bounded against Gazebo truth.",
        "Stock 122327 is finally genuinely GNSS-denied and stock optical flow fuses, but it is not accepted because the stock rangefinder stream is unreliable.",
        "The most important plots are the route overlay, GNSS fix/satellites, optical-flow fusion fraction, distance sensor, and horizontal/height error plots.",
    ]:
        sections.append(f"<li>{html.escape(item)}</li>")
    sections.append("</ul>")
    sections.append("<h2>Contact Sheet</h2>")
    sections.append(f"<img class=\"plot\" src=\"{html.escape(contact_sheet.name)}\" alt=\"Phase 11 plot contact sheet\">")
    for idx, note in enumerate(PLOTS, start=1):
        sections.append(f"<section class=\"card\"><h2>{idx}. {html.escape(note.title)}</h2>")
        sections.append(f"<img class=\"plot\" src=\"plots/{html.escape(note.filename)}\" alt=\"{html.escape(note.title)}\">")
        for label, text in [
            ("What it proves", note.proves),
            ("How to read it", note.read),
            ("Conclusion", note.conclusion),
            ("Caveat", note.caveat),
        ]:
            sections.append(f"<p class=\"note\"><span class=\"label\">{html.escape(label)}:</span> {html.escape(text)}</p>")
        sections.append("</section>")
    sections.append("</body></html>")
    out = out_dir / "phase11_plot_walkthrough.html"
    out.write_text("\n".join(sections) + "\n")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    plot_dir = copy_plots(args.out_dir)
    contact_sheet = make_contact_sheet(args.out_dir, plot_dir)
    report = write_markdown(args.out_dir, plot_dir, contact_sheet)
    html_report = write_html(args.out_dir, plot_dir, contact_sheet)
    print(report)
    print(html_report)
    print(contact_sheet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
