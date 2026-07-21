#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

from PIL import Image


def require_int(text: str, field: str) -> int:
    match = re.search(rf"^{re.escape(field)}:\s*(\d+)\s*$", text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"missing {field}")
    return int(match.group(1))


def optional_symbol(text: str, field: str, default: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*([A-Z0-9_]+)\s*$", text, flags=re.MULTILINE)
    return match.group(1) if match else default


def extract_data_bytes(text: str) -> bytes:
    match = re.search(r'^data:\s*"((?:\\.|[^"\\])*)"\s*$', text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("missing data field")

    # Gazebo prints protobuf bytes as a text-format escaped string. Python's
    # bytes literal parser understands the same octal escapes.
    return ast.literal_eval('b"' + match.group(1) + '"')


def image_from_gz_textproto(text: str) -> Image.Image:
    width = require_int(text, "width")
    height = require_int(text, "height")
    step = require_int(text, "step")
    pixel_format = optional_symbol(text, "pixel_format_type", "RGB_INT8")
    data = extract_data_bytes(text)

    formats = {
        "RGB_INT8": ("RGB", "RGB", 3),
        "BGR_INT8": ("RGB", "BGR", 3),
        "RGBA_INT8": ("RGBA", "RGBA", 4),
        "BGRA_INT8": ("RGBA", "BGRA", 4),
        "L_INT8": ("L", "L", 1),
    }
    if pixel_format not in formats:
        raise ValueError(f"unsupported pixel_format_type: {pixel_format}")

    mode, raw_mode, channels = formats[pixel_format]
    expected_min = height * step
    if len(data) < expected_min:
        raise ValueError(f"image data too short: got {len(data)} bytes, expected at least {expected_min}")

    row_bytes = width * channels
    if step == row_bytes:
        payload = data[: height * row_bytes]
    else:
        rows = [data[i * step : i * step + row_bytes] for i in range(height)]
        payload = b"".join(rows)

    return Image.frombytes(mode, (width, height), payload, "raw", raw_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Gazebo gz.msgs.Image textproto sample to PNG.")
    parser.add_argument("input", help="Path to camera_image_sample.txt from a run folder")
    parser.add_argument("output", help="Output PNG path")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    text = input_path.read_text(errors="surrogateescape")
    image = image_from_gz_textproto(text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"wrote {output_path} ({image.width}x{image.height}, mode={image.mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
