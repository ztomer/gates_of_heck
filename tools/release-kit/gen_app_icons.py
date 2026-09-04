#!/usr/bin/env python3
"""tools/release-kit/gen_app_icons.py — ONE icon pipeline replacing five
per-repo generators (ZeroThunder + routines swift renderers, Taxes PIL
compositor, divoom sips loop, CadGoose AppKit compositor).

This tool is the SIZE-LADDER half: given a source image (a 1024x1024 PNG, or
any image sips can read — it is normalized to true PNG first), it emits the
full Apple iconset ladder and runs iconutil to produce a .icns. Drawing the
artwork itself stays per-repo (that is design, not plumbing); feed this script
the finished 1024px source.

Output (deterministic order — the fixed Apple ladder, never directory-scan
order):
    OUT/<Name>.iconset/icon_16x16.png      16      (kept: auditable/reproducible)
    OUT/<Name>.iconset/icon_16x16@2x.png   32
    ... all ten members through icon_512x512@2x.png 1024
    OUT/<Name>.icns                        via iconutil

Options:
    --appiconset     also write OUT/<Name>.appiconset/ in the MODERN single-
                     size format (one universal 1024 asset — current Xcode
                     templates)
    --legacy-ladder  with --appiconset: the last full multi-size iOS set
                     instead (iphone/ipad/ios-marketing idioms, every PNG
                     rendered into the group)
    --name NAME      basename (default: AppIcon)

Usage:
    python3 tools/release-kit/gen_app_icons.py assets/icon-1024.png packaging/
Requires macOS sips + iconutil (both ship with the OS).

Kept from ancestors: divoom's normalize-via-sips (sources were JPEG bytes
misnamed .png), the fixed (name, px) ladder table shared by ZeroThunder,
Taxes and CadGoose, iconutil as the only icns encoder, and its habit of
keeping the .iconset around so output stays auditable. Dropped: per-tool
drawing code, Taxes' Windows .ico side output, font/gradient composition.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

# Apple's required iconset members: (filename, pixel size). Fixed order.
LADDER = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]

# Last full multi-size iOS appiconset: (filename, pt size, scale, idiom).
LEGACY_SET = [
    ("icon_20x20@2x.png", 20, 2, "iphone"),
    ("icon_20x20@3x.png", 20, 3, "iphone"),
    ("icon_29x29@2x.png", 29, 2, "iphone"),
    ("icon_29x29@3x.png", 29, 3, "iphone"),
    ("icon_40x40@2x.png", 40, 2, "iphone"),
    ("icon_40x40@3x.png", 40, 3, "iphone"),
    ("icon_60x60@2x.png", 60, 2, "iphone"),
    ("icon_60x60@3x.png", 60, 3, "iphone"),
    ("icon_20x20.png", 20, 1, "ipad"),
    ("icon_20x20@2x.png", 20, 2, "ipad"),
    ("icon_29x29.png", 29, 1, "ipad"),
    ("icon_29x29@2x.png", 29, 2, "ipad"),
    ("icon_40x40.png", 40, 1, "ipad"),
    ("icon_40x40@2x.png", 40, 2, "ipad"),
    ("icon_76x76.png", 76, 1, "ipad"),
    ("icon_76x76@2x.png", 76, 2, "ipad"),
    ("icon_83.5x83.5@2x.png", 83.5, 2, "ipad"),
    ("icon_1024x1024.png", 1024, 1, "ios-marketing"),
]


def die(msg: str) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1)


def png_dimensions(path: Path) -> tuple[int, int]:
    """Width/height straight from the IHDR chunk — no image library needed."""
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        die(f"not a PNG: {path}")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def sips_scale(base: Path, px: int, dest: Path) -> None:
    r = subprocess.run(
        ["sips", "-z", str(px), str(px), str(base), "--out", str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(f"sips → {dest.name} failed: {r.stderr.strip()}")
    got = png_dimensions(dest)
    if got != (px, px):
        die(f"{dest.name}: expected {px}x{px}, sips produced {got[0]}x{got[1]}")


def normalize_source(src: Path, work: Path) -> Path:
    """True-PNG copy of whatever we were handed."""
    if shutil.which("sips") is None:
        die("sips not found (macOS only)")
    base = work / "base.png"
    r = subprocess.run(
        ["sips", "-s", "format", "png", str(src), "--out", str(base)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(f"sips could not read {src}: {r.stderr.strip()}")
    return base


def modern_contents_json() -> dict:
    """Current single-size format: one universal 1024 asset."""
    return {
        "images": [
            {"filename": "icon_1024x1024.png", "idiom": "universal",
             "platform": "ios", "size": "1024x1024"}
        ],
        "info": {"author": "xcode", "version": 1},
    }


def legacy_contents_json() -> dict:
    return {
        "images": [
            {"filename": fname, "idiom": idiom, "scale": f"{scale}x",
             "size": f"{size}x{size}"}
            for fname, size, scale, idiom in LEGACY_SET
        ],
        "info": {"author": "xcode", "version": 1},
    }


def write_json(path: Path, spec: dict) -> None:
    # Deterministic field order, trailing newline — regenerating is a no-op diff.
    path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Emit the full Apple icon ladder + .icns")
    ap.add_argument("source", type=Path, help="1024x1024 source image (PNG preferred)")
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--name", default="AppIcon")
    ap.add_argument("--appiconset", action="store_true",
                    help="also write <name>.appiconset/ (modern single-size)")
    ap.add_argument("--legacy-ladder", action="store_true",
                    help="with --appiconset: full multi-size iOS set instead")
    args = ap.parse_args(argv)

    src = args.source.resolve()
    if not src.is_file():
        die(f"source image not found: {src}")
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="goh-icons-"))
    try:
        base = normalize_source(src, work)
        w, h = png_dimensions(base)
        largest = max(px for _, px in LADDER)
        if max(w, h) < largest:
            print(f"⚠ source is {w}x{h}, smaller than {largest}px — top ladder "
                  f"members will be upscaled (blurry); prefer a 1024px source")

        # The ladder, in fixed order, into an auditable (kept) iconset dir.
        iconset = out_dir / f"{args.name}.iconset"
        iconset.mkdir(exist_ok=True)
        for fname, px in LADDER:
            dest = iconset / fname
            sips_scale(base, px, dest)
            print(f"→ {fname}  ({px}x{px})")

        icns = out_dir / f"{args.name}.icns"
        if shutil.which("iconutil") is None:
            print("⚠ iconutil not found — skipping .icns (iconset members are written)")
        else:
            r = subprocess.run(
                ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                die(f"iconutil failed: {r.stderr.strip()}")
            print(f"✓ {icns}")

        if args.appiconset:
            group = out_dir / f"{args.name}.appiconset"
            group.mkdir(exist_ok=True)
            if args.legacy_ladder:
                for fname, _size, scale, _idiom in LEGACY_SET:
                    px = int(_size * scale)
                    if not (group / fname).exists():
                        sips_scale(base, px, group / fname)
                write_json(group / "Contents.json", legacy_contents_json())
            else:
                shutil.copy2(iconset / "icon_512x512@2x.png",
                             group / "icon_1024x1024.png")
                write_json(group / "Contents.json", modern_contents_json())
            print(f"✓ {group / 'Contents.json'}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
