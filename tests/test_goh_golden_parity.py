"""Golden parity: `goh golden` agrees with `lib/golden_core.py`, to the bit.

Every metric must be BIT-identical to the reference's Pillow/numpy tier —
the numbers consumers calibrate their tolerances against. The integer
metrics are exact in any summation order; `ssim` reduces floats, and the
port reproduces numpy's pairwise summation order (crates/goh-golden
`numeric.rs`) so its rounding lands on the same bit rather than a few ulps
away. Verdicts, failure strings (tolerances printed as Python's `repr`) and
exit codes agree too.

The fixtures cover every decode path Pillow takes differently: palette and
1-bit images expand to their colours, 16-bit RGB keeps the high byte, and
16-bit grayscale alone CLAMPS to 255. The sizes include the plan's three
(40 kpx, 1 Mpx, 6 Mpx).
"""
from __future__ import annotations

import json
import os
import random
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "lib" / "golden_core.py"

try:
    from PIL import Image as PILImage

    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

try:
    import numpy as np

    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

# The reference's numbers ARE the numpy tier's: without numpy it computes in
# pure Python, a different summation order, and parity is not bit-exact.
needs_reference = pytest.mark.skipif(not (HAVE_PIL and HAVE_NUMPY), reason="the reference tier needs Pillow + numpy")

# Fraction of pixels a fixture perturbs, and by how much.
WOBBLE_FRACTION = 0.05
WOBBLE = 30


def wobble(rng: random.Random, value: int, depth_max: int) -> int:
    return (value + rng.randrange(-WOBBLE, WOBBLE + 1)) % (depth_max + 1)


def make_pair(tmp_path: Path, name: str, w: int, h: int, mode: str, seed: int, *, every: bool = False):
    """Two Pillow images in `mode`, the second perturbed in a few (or every) pixel."""
    rng = random.Random(seed)
    a, b = PILImage.new(mode, (w, h)), PILImage.new(mode, (w, h))
    bands = len(a.getbands())
    top = 1 if mode == "1" else 255
    pxa, pxb = a.load(), b.load()
    for y in range(h):
        for x in range(w):
            v = tuple(rng.randrange(top + 1) for _ in range(bands))
            pxa[x, y] = v[0] if bands == 1 else v
            if every or rng.random() < WOBBLE_FRACTION:
                wob = tuple(wobble(rng, c, top) for c in v)
                pxb[x, y] = wob[0] if bands == 1 else wob
            else:
                pxb[x, y] = pxa[x, y]
    if mode == "P":
        palette = [rng.randrange(256) for _ in range(256 * 3)]
        a.putpalette(palette)
        b.putpalette(palette)
    pa, pb = tmp_path / f"{name}_a.png", tmp_path / f"{name}_b.png"
    a.save(pa)
    b.save(pb)
    return pa, pb


def write_png16(path: Path, w: int, h: int, color_type: int, samples: list[int], trns: int | None = None) -> None:
    """A 16-bit PNG written by hand: Pillow cannot write every 16-bit layout."""
    channels = {0: 1, 2: 3}[color_type]
    row_len = w * channels
    raw = b"".join(
        b"\x00" + b"".join(struct.pack(">H", v) for v in samples[r * row_len:(r + 1) * row_len]) for r in range(h)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0)
    # A gray tRNS names one transparent sample value; the decoder then expands to gray+alpha.
    extra = chunk(b"tRNS", struct.pack(">H", trns)) if trns is not None else b""
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + extra + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_pair16(tmp_path: Path, name: str, w: int, h: int, color_type: int, seed: int, trns: int | None = None):
    rng = random.Random(seed)
    channels = {0: 1, 2: 3}[color_type]
    a = [rng.randrange(65536) if rng.random() < 0.5 else rng.randrange(512) for _ in range(w * h * channels)]
    b = [v if rng.random() > WOBBLE_FRACTION else rng.randrange(65536) for v in a]
    pa, pb = tmp_path / f"{name}_a.png", tmp_path / f"{name}_b.png"
    write_png16(pa, w, h, color_type, a, trns)
    write_png16(pb, w, h, color_type, b, trns)
    return pa, pb


def make_pair_numpy(tmp_path: Path, name: str, w: int, h: int, seed: int):
    """A large RGB pair generated with numpy — the per-pixel loop is too slow at 6 Mpx."""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    b = a.copy()
    mask = rng.random((h, w)) < WOBBLE_FRACTION
    b[mask] = (a[mask].astype(np.int16) + rng.integers(-WOBBLE, WOBBLE + 1, size=(mask.sum(), 3))) % 256
    pa, pb = tmp_path / f"{name}_a.png", tmp_path / f"{name}_b.png"
    PILImage.fromarray(a).save(pa)
    PILImage.fromarray(b).save(pb)
    return pa, pb


def run(cmd: list[str], env: dict | None = None) -> tuple[int, dict | None, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else None), r.stderr


def run_py(a: Path, b: Path, tolerances: str = "{}"):
    return run(["python3", str(GOLDEN), str(a), str(b), "--tolerances", tolerances, "--json"])


def run_goh(goh: Path, a: Path, b: Path, tolerances: str = "{}"):
    env = dict(os.environ, GOH_DIR=str(ROOT))
    return run([str(goh), "golden", str(a), str(b), "--tolerances", tolerances, "--json"], env)


def assert_same_verdict(py: dict, rs: dict) -> None:
    # Tiers differ by design (pillow/numpy vs png-crate/native); every number,
    # every failure string and the verdict must not.
    for key, value in py["metrics"].items():
        assert value == rs["metrics"][key], f"{key}: reference {value!r} vs native {rs['metrics'][key]!r}"
    assert py["metrics"].keys() == rs["metrics"].keys()
    assert py["tolerances"] == rs["tolerances"]
    assert py["failures"] == rs["failures"]
    assert (py["ok"], py["identical"], py["size"]) == (rs["ok"], rs["identical"], rs["size"])


CASES = [
    ("tiny", 16, 16, "RGB", 7),
    ("small", 64, 48, "RGB", 8),
    ("mid_40kpx", 200, 200, "RGB", 9),
    ("alpha", 200, 200, "RGBA", 10),
    ("gray", 200, 200, "L", 11),
    ("gray_alpha", 64, 64, "LA", 16),
    ("palette", 120, 90, "P", 17),
    ("one_bit", 64, 64, "1", 18),
    ("big_1mpx", 1000, 1000, "RGB", 12),
]


@pytest.mark.parametrize("name,w,h,mode,seed", CASES)
@needs_reference
def test_metrics_agree(goh: Path, tmp_path: Path, name: str, w: int, h: int, mode: str, seed: int):
    pa, pb = make_pair(tmp_path, name, w, h, mode, seed)
    code_py, py, _ = run_py(pa, pb)
    code_goh, rs, _ = run_goh(goh, pa, pb)
    assert code_py == code_goh, (code_py, code_goh)
    assert_same_verdict(py, rs)


# gray16_trns: a tRNS chunk makes the decoder hand 16-bit gray back as gray+alpha, and Pillow
# still clamps it — found by the goh-golden unit tests, pinned here against Pillow itself.
@pytest.mark.parametrize("name,color_type,seed,trns", [("gray16", 0, 21, None), ("rgb16", 2, 22, None),
                                                       ("gray16_trns", 0, 23, 128)])
@needs_reference
def test_sixteen_bit_decodes_as_pillow_does(goh: Path, tmp_path: Path, name: str, color_type: int, seed: int,
                                            trns: int | None):
    pa, pb = make_pair16(tmp_path, name, 50, 40, color_type, seed, trns)
    code_py, py, _ = run_py(pa, pb)
    code_goh, rs, _ = run_goh(goh, pa, pb)
    assert code_py == code_goh
    assert_same_verdict(py, rs)


@needs_reference
def test_a_failing_mean_prints_its_tolerance_as_python_does(goh: Path, tmp_path: Path) -> None:
    pa, pb = make_pair(tmp_path, "heavy", 64, 64, "RGB", 19, every=True)
    code_py, py, _ = run_py(pa, pb)
    code_goh, rs, _ = run_goh(goh, pa, pb)
    assert code_py == code_goh == 1
    assert any(f.startswith("mean_abs_diff") and f.endswith("> 8.0") for f in py["failures"]), py["failures"]
    assert_same_verdict(py, rs)


@pytest.mark.slow
@needs_reference
def test_six_megapixels_agree(goh: Path, tmp_path: Path) -> None:
    pa, pb = make_pair_numpy(tmp_path, "huge_6mpx", 3000, 2000, 20)
    code_py, py, _ = run_py(pa, pb)
    code_goh, rs, _ = run_goh(goh, pa, pb)
    assert code_py == code_goh
    assert_same_verdict(py, rs)


@needs_reference
def test_identical_passes_on_both(goh: Path, tmp_path: Path) -> None:
    pa, _ = make_pair(tmp_path, "idem", 64, 64, "RGB", 13)
    code_py, py, _ = run_py(pa, pa)
    code_goh, rs, _ = run_goh(goh, pa, pa)
    assert (code_py, code_goh) == (0, 0)
    assert_same_verdict(py, rs)


@needs_reference
def test_tolerances_and_preconditions_agree(goh: Path, tmp_path: Path) -> None:
    pa, pb = make_pair(tmp_path, "tol", 64, 64, "RGB", 14)
    loose = '{"mean_abs_diff_max": 100, "changed_frac_max": 1, "ssim_min": 0}'
    assert run_py(pa, pb, loose)[0] == run_goh(goh, pa, pb, loose)[0] == 0
    # Unknown key, non-number, boolean, NaN, not an object, not JSON: a
    # precondition on both, reported on stderr with nothing on stdout.
    for bad in ['{"nope": 1}', '{"ssim_min": "high"}', '{"ssim_min": true}', '{"ssim_min": NaN}', "[1]", "{"]:
        code_py, out_py, _ = run_py(pa, pb, bad)
        code_goh, out_goh, _ = run_goh(goh, pa, pb, bad)
        assert (code_py, code_goh) == (2, 2), bad
        assert out_py is None and out_goh is None, bad
    # The messages name the problem the same way where they can.
    _, _, err_py = run_py(pa, pb, '{"ssim_min": "high"}')
    _, _, err_goh = run_goh(goh, pa, pb, '{"ssim_min": "high"}')
    assert err_py.strip() == err_goh.strip()
    _, _, err_py = run_py(pa, pb, '{"nope": 1}')
    _, _, err_goh = run_goh(goh, pa, pb, '{"nope": 1}')
    assert err_py.strip() == err_goh.strip()
    # Size mismatch: precondition on both.
    pc, _ = make_pair(tmp_path, "sz", 32, 32, "RGB", 15)
    assert run_py(pa, pc)[0] == run_goh(goh, pa, pc)[0] == 2
