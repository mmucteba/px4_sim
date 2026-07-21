#!/usr/bin/env python3
import argparse
import csv
import re
from pathlib import Path

NUM = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

def parse_number(block: str, field: str, default: float) -> float:
    m = re.search(rf"\b{re.escape(field)}:\s*({NUM})", block)
    return float(m.group(1)) if m else default

def parse_subblock(block: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\s*\{{(.*?)\}}", block, re.S)
    return m.group(1) if m else ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Raw text captured from `gz topic -e`")
    ap.add_argument("--output-csv", required=True)
    ap.add_argument("--model-name", default="x500_0")
    args = ap.parse_args()

    text = Path(args.input).read_text(errors="replace").splitlines()

    rows = []
    current_sec = None
    current_nsec = None
    in_stamp = False

    i = 0
    while i < len(text):
        line = text[i].strip()

        if line == "stamp {":
            in_stamp = True
            i += 1
            continue

        if in_stamp:
            if line.startswith("sec:"):
                current_sec = int(line.split(":", 1)[1].strip())
            elif line.startswith("nsec:"):
                current_nsec = int(line.split(":", 1)[1].strip())
            elif line == "}":
                in_stamp = False
            i += 1
            continue

        if line == "pose {":
            depth = 1
            block_lines = [text[i]]
            i += 1
            while i < len(text) and depth > 0:
                block_lines.append(text[i])
                depth += text[i].count("{")
                depth -= text[i].count("}")
                i += 1

            block = "\n".join(block_lines)

            name_match = re.search(r'name:\s*"([^"]+)"', block)
            name = name_match.group(1) if name_match else ""

            if name == args.model_name and current_sec is not None and current_nsec is not None:
                pos = parse_subblock(block, "position")
                ori = parse_subblock(block, "orientation")

                sim_time_s = current_sec + current_nsec / 1e9

                rows.append({
                    "sim_sec": current_sec,
                    "sim_nsec": current_nsec,
                    "sim_time_s": sim_time_s,
                    "name": name,
                    "x": parse_number(pos, "x", 0.0),
                    "y": parse_number(pos, "y", 0.0),
                    "z": parse_number(pos, "z", 0.0),
                    "qx": parse_number(ori, "x", 0.0),
                    "qy": parse_number(ori, "y", 0.0),
                    "qz": parse_number(ori, "z", 0.0),
                    "qw": parse_number(ori, "w", 1.0),
                })
            continue

        i += 1

    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = ["sim_sec", "sim_nsec", "sim_time_s", "name", "x", "y", "z", "qx", "qy", "qz", "qw"]
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"OK: wrote {len(rows)} row(s) to {out}")
    if rows:
        print(f"First sim_time_s: {rows[0]['sim_time_s']:.6f}")
        print(f"Last sim_time_s: {rows[-1]['sim_time_s']:.6f}")
        print(f"Duration_s: {rows[-1]['sim_time_s'] - rows[0]['sim_time_s']:.6f}")
    else:
        raise SystemExit("ERROR: no matching pose rows found")

if __name__ == "__main__":
    main()
