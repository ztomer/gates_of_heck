"""Shared fixture vocabulary for the two golden_core suites.

One suite pins the pixel-diff MATH (metrics, tolerance gating, the minimal PNG decoder, the
pure-Python fallback); the other pins the CLI that wraps it. Both need the same synthetic images and
the same three ways of running the code — through Pillow, through the pure-Python math, and through
`golden_core.py` as a subprocess — so the builders live here and neither suite grows a second
definition of "a 64x64 grey PNG".

Every fixture is synthetic and built programmatically: no app launches, no image files in the repo.
"""

import struct
import subprocess
import sys
import zlib

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import golden_core  # noqa: E402

LIB = REPO_ROOT / "lib" / "golden_core.py"

try:
    from PIL import Image as PILImage

    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


# ---- fixture builders --------------------------------------------------------


def make_png(path, w, h, pixel_fn):
    """Write an RGB PNG whose (x, y) pixels come from pixel_fn."""
    img = PILImage.new("RGB", (w, h))
    img.putdata([pixel_fn(x, y) for y in range(h) for x in range(w)])
    img.save(path)
    return path


def flat_png(path, w, h, rgb):
    """Hand-built filter-0 RGB PNG — exercises the minimal decoder with zero PIL involvement."""

    def chunk(ctype, body):
        return (
            struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFF_FF_FF_FF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)
    return path


def load(path):
    return golden_core.load_rgb(str(path))


@pytest.fixture
def plain(tmp_path):
    return make_png(tmp_path / "plain.png", 64, 64, lambda x, y: (100, 100, 100))


def corrupt_png(ihdr_body=None, raw=None):
    def chunk(ctype, body):
        return (
            struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFF_FF_FF_FF)
        )

    ihdr = ihdr_body if ihdr_body is not None else struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)
    stream = raw if raw is not None else b"".join(b"\x00" + bytes(12) for _ in range(3))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(stream))
        + chunk(b"IEND", b"")
    )


def decode_minimal(data):
    orig = golden_core._HAVE_PIL
    golden_core._HAVE_PIL = False
    try:
        return golden_core._decode_png_minimal(data, "corrupt.png")
    finally:
        golden_core._HAVE_PIL = orig


# ---- the CLI, run as a subprocess --------------------------------------------


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(LIB), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        check=False,
    )
