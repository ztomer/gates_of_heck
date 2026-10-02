"""tools/release-kit/gen_app_icons.py — the Apple icon ladder, generated.

Split from `test_release_kit.py` by concern rather than for length alone: that file is the release
FLOW (release.sh, its steps, its idempotency, its tap bump) and this one is a generator whose
output is read out of PNG IHDR bytes rather than out of a git log. Neither needs the other's fixture.

Sizes are verified from the bytes the generator actually wrote, not from the names it claims, so a
ladder that is right on paper and wrong on disk fails here. macOS tools (sips, iconutil) gate the
class, the same way they gate the script.
"""

import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from conftest import REPO_ROOT

GEN_ICONS = REPO_ROOT / "tools" / "release-kit" / "gen_app_icons.py"


def png_bytes(w: int, h: int) -> bytes:
    """A minimal valid RGBA PNG built by hand (no image library dependency)."""
    raw = b"".join(b"\x00" + b"\x40\x80\xc0\xff" * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def png_dims(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


EXPECTED_LADDER = [
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


def run_gen(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(GEN_ICONS), *args], capture_output=True, text=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="sips/iconutil are macOS tools")
class TestGenIcons:
    @pytest.fixture
    def generated(self, tmp_path: Path) -> Path:
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(64, 64))
        out = tmp_path / "out"
        r = run_gen(str(src), str(out), "--appiconset", "--name", "Demo")
        assert r.returncode == 0, r.stdout + r.stderr
        return out

    def test_iconset_contains_exactly_the_apple_ladder(self, generated):
        iconset = generated / "Demo.iconset"
        names = sorted(p.name for p in iconset.iterdir())
        assert names == sorted(n for n, _ in EXPECTED_LADDER)

    def test_each_member_has_the_right_pixel_size(self, generated):
        iconset = generated / "Demo.iconset"
        for fname, px in EXPECTED_LADDER:
            assert png_dims(iconset / fname) == (px, px), fname

    def test_icns_is_produced(self, generated):
        icns = generated / "Demo.icns"
        assert icns.is_file() and icns.stat().st_size > 0

    def test_modern_appiconset_contents_json(self, generated):
        spec = json.loads((generated / "Demo.appiconset" / "Contents.json").read_text())
        images = spec["images"]
        assert images == [
            {
                "filename": "icon_1024x1024.png",
                "idiom": "universal",
                "platform": "ios",
                "size": "1024x1024",
            }
        ]
        assert (generated / "Demo.appiconset" / "icon_1024x1024.png").is_file()

    def test_legacy_ladder_contents_json_matches_rendered_pngs(self, tmp_path):
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(32, 32))
        out = tmp_path / "out"
        r = run_gen(str(src), str(out), "--appiconset", "--legacy-ladder", "--name", "L")
        assert r.returncode == 0, r.stdout + r.stderr
        group = out / "L.appiconset"
        spec = json.loads((group / "Contents.json").read_text())
        entries = spec["images"]
        assert {e["idiom"] for e in entries} == {"iphone", "ipad", "ios-marketing"}
        for e in entries:
            assert (group / e["filename"]).is_file(), e["filename"]
            scale = int(e["scale"].rstrip("x"))
            pt = float(e["size"].split("x")[0])
            assert png_dims(group / e["filename"]) == (round(pt * scale),) * 2

    def test_deterministic_output_is_a_no_op_diff(self, tmp_path):
        src = tmp_path / "src.png"
        src.write_bytes(png_bytes(64, 64))
        out1, out2 = tmp_path / "one", tmp_path / "two"
        assert run_gen(str(src), str(out1)).returncode == 0
        assert run_gen(str(src), str(out2)).returncode == 0
        names1 = sorted(p.relative_to(out1) for p in out1.rglob("*") if p.is_file())
        names2 = sorted(p.relative_to(out2) for p in out2.rglob("*") if p.is_file())
        assert names1 == names2
        for rel in names1:
            assert (out1 / rel).read_bytes() == (out2 / rel).read_bytes(), rel
