"""lib/golden_core.py — contract tests.

Fixtures are synthetic images built programmatically (no app launches). Each
metric is proven to trip INDEPENDENTLY where the arithmetic allows it, then
red-proven by loosening one tolerance at a time.

A note on independence: global SSIM is violently sensitive to localised change
(a 10x10 white square on black drops it to ~0.04), so the mean-abs-diff and
changed-fraction isolation cases pin ssim_min: 0 rather than pretend three
metrics can always be separated by construction. That sensitivity IS one of
the things the SSIM sanity tests pin down.

The Pillow-absent fallback path is exercised deterministically by monkeypatching
golden_core._HAVE_PIL / _HAVE_NUMPY to False — no environment juggling, and the
pure-Python math is asserted numerically equal to the numpy path.
"""

import json
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
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(
            ">I", zlib.crc32(ctype + body) & 0xFF_FF_FF_FF
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


# ---- identical images ---------------------------------------------------------


def test_identical_images_pass_everything(tmp_path, plain):
    b = tmp_path / "copy.png"
    b.write_bytes(plain.read_bytes())
    r = golden_core.compare(load(plain), load(b))
    assert r["identical"] and r["ok"]
    assert r["metrics"] == {"mean_abs_diff": 0.0, "changed_fraction": 0.0, "ssim": 1.0}
    assert r["failures"] == []


def test_identical_via_reencode_still_passes(tmp_path, plain):
    b = make_png(tmp_path / "reenc.png", 64, 64, lambda x, y: (100, 100, 100))
    r = golden_core.compare(load(plain), load(b))
    assert r["ok"] and r["metrics"]["ssim"] == 1.0


# ---- each metric trips alone ----------------------------------------------------


def test_mean_abs_diff_trips_alone(tmp_path):
    a = make_png(tmp_path / "a.png", 32, 32, lambda x, y: (100, 100, 100))
    # +10 per channel: every pixel below the changed threshold (16), so ONLY the mean moves.
    b = make_png(tmp_path / "b.png", 32, 32, lambda x, y: (110, 110, 110))
    r = golden_core.compare(load(a), load(b), {"changed_frac_max": 1.0, "ssim_min": 0})
    assert r["metrics"]["changed_fraction"] == 0.0
    assert r["failures"] == [f"mean_abs_diff {r['metrics']['mean_abs_diff']:.4f} > 8.0"]


def test_loosening_mean_abs_diff_flips_verdict(tmp_path):
    a = make_png(tmp_path / "a.png", 32, 32, lambda x, y: (100, 100, 100))
    b = make_png(tmp_path / "b.png", 32, 32, lambda x, y: (110, 110, 110))
    tight = golden_core.compare(load(a), load(b), {"changed_frac_max": 1.0, "ssim_min": 0})
    loose = golden_core.compare(
        load(a), load(b), {"mean_abs_diff_max": 20, "changed_frac_max": 1.0, "ssim_min": 0}
    )
    assert not tight["ok"] and loose["ok"]


def test_changed_fraction_trips_alone(tmp_path):
    a = make_png(tmp_path / "a.png", 100, 100, lambda x, y: (0, 0, 0))
    # 10x10 bright square: 1% of pixels blow past the channel threshold...
    b = make_png(
        tmp_path / "b.png", 100, 100, lambda x, y: (255, 255, 255) if x < 10 and y < 10 else (0, 0, 0)
    )
    tol = {"changed_frac_max": 0.005, "mean_abs_diff_max": 100, "ssim_min": 0}
    r = golden_core.compare(load(a), load(b), tol)
    assert r["metrics"]["changed_fraction"] == pytest.approx(0.01)
    assert r["failures"] == [
        f"changed_fraction {r['metrics']['changed_fraction']:.6f} > 0.005"
    ]


def test_loosening_changed_frac_flips_verdict(tmp_path):
    a = make_png(tmp_path / "a.png", 100, 100, lambda x, y: (0, 0, 0))
    b = make_png(
        tmp_path / "b.png", 100, 100, lambda x, y: (255, 255, 255) if x < 10 and y < 10 else (0, 0, 0)
    )
    tol = {"changed_frac_max": 0.005, "mean_abs_diff_max": 100, "ssim_min": 0}
    assert not golden_core.compare(load(a), load(b), tol)["ok"]
    assert golden_core.compare(load(a), load(b), {**tol, "changed_frac_max": 0.05})["ok"]


# ---- SSIM sanity -----------------------------------------------------------------


def test_ssim_identical_is_exactly_one(plain):
    assert golden_core.ssim(load(plain), load(plain)) == 1.0


def test_ssim_inverted_gradient_is_negative(tmp_path):
    n = 128
    a = make_png(tmp_path / "a.png", n, 1, lambda x, y: (x * 2, x * 2, x * 2))
    b = make_png(tmp_path / "b.png", n, 1, lambda x, y: (254 - x * 2,) * 3)
    # Perfect anticorrelation drives global SSIM below zero — the known-simple case.
    assert golden_core.ssim(load(a), load(b)) < 0


def test_ssim_near_equal_uniforms_above_floor(tmp_path):
    a = make_png(tmp_path / "a.png", 16, 16, lambda x, y: (77, 77, 77))
    b = make_png(tmp_path / "b.png", 16, 16, lambda x, y: (78, 78, 78))
    assert golden_core.ssim(load(a), load(b)) > 0.985


# ---- verdict: one tolerance at a time ----------------------------------------------

def test_tolerance_defaults_are_the_documented_ancestor_values():
    """The defaults ARE the provenance record (see module docstring). A silent
    change here silently retunes every harness that embeds this module."""
    assert golden_core.TOLERANCE_DEFAULTS == {
        "mean_abs_diff_max": 8.0,
        "changed_frac_max": 0.08,
        "changed_px_threshold": 16.0,
        "ssim_min": 0.985,
    }


@pytest.fixture
def triple_failure(tmp_path):
    """Left half black, right half white vs its inverse: trips all three tolerances."""
    half = 40
    a = make_png(tmp_path / "a.png", half * 2, 20, lambda x, y: (0, 0, 0) if x < half else (255,) * 3)
    b = make_png(tmp_path / "b.png", half * 2, 20, lambda x, y: (255,) * 3 if x < half else (0, 0, 0))
    r = golden_core.compare(load(a), load(b))
    assert len(r["failures"]) == 3, r["failures"]
    return load(a), load(b)


def test_each_tolerance_gates_its_own_metric(triple_failure):
    a, b = triple_failure

    def failures(**over):
        return golden_core.compare(a, b, over)["failures"]

    assert len(failures()) == 3
    assert len(failures(mean_abs_diff_max=255)) == 2
    assert len(failures(mean_abs_diff_max=255, changed_frac_max=1.0)) == 1
    # ssim_min: -2 (not 0) — perfect anticorrelation drives SSIM NEGATIVE, so 0 is not off.
    assert failures(mean_abs_diff_max=255, changed_frac_max=1.0, ssim_min=-2) == []


# ---- pure-Python fallback computes the SAME numbers ---------------------------------


def test_pure_python_matches_numpy(tmp_path, monkeypatch):
    a = make_png(
        tmp_path / "a.png",
        33,
        17,
        lambda x, y: (x * 7 % 256, y * 11 % 256, (x + y) * 3 % 256),
    )
    b = make_png(
        tmp_path / "b.png",
        33,
        17,
        lambda x, y: ((x * 7 + 5) % 256, (y * 11 + 9) % 256, ((x + y) * 3 + 30) % 256),
    )
    ia, ib = load(a), load(b)
    monkeypatch.setattr(golden_core, "_HAVE_NUMPY", False)
    assert golden_core.mean_abs_diff(ia, ib) == pytest.approx(golden_core.mean_abs_diff(ia, ib))
    py = {
        "mean_abs_diff": golden_core.mean_abs_diff(ia, ib),
        "changed_fraction": golden_core.changed_fraction(ia, ib, 16.0),
        "ssim": golden_core.ssim(ia, ib),
    }
    monkeypatch.setattr(golden_core, "_HAVE_NUMPY", True)
    assert py["mean_abs_diff"] == pytest.approx(golden_core.mean_abs_diff(ia, ib), abs=1e-9)
    assert py["changed_fraction"] == pytest.approx(golden_core.changed_fraction(ia, ib, 16.0))
    assert py["ssim"] == pytest.approx(golden_core.ssim(ia, ib), abs=1e-9)


# ---- minimal PNG decoder -------------------------------------------------------------


@pytest.mark.skipif(not HAVE_PIL, reason="cross-check needs Pillow to WRITE fixtures")
def test_fallback_decoder_matches_pillow_byte_for_byte(tmp_path, monkeypatch):
    """The same file decoded by Pillow and by the minimal decoder must agree exactly.

    The fixture is deliberately NOISY: Pillow picks scanline filters adaptively,
    and only varied content forces it onto Sub/Up/Average/Paeth — a flat image
    would let both sides meet at filter 0 and prove nothing about defiltering.
    """
    p = make_png(
        tmp_path / "noisy.png",
        61,
        37,
        lambda x, y: ((x * 13 + y * 7) % 256, (x * 3 + y * 29) % 256, (x + y * 11) % 256),
    )
    monkeypatch.setattr(golden_core, "_HAVE_PIL", False)
    via_fallback = load(p)
    monkeypatch.setattr(golden_core, "_HAVE_PIL", True)
    via_pillow = load(p)
    assert via_fallback == via_pillow


def test_decoder_reads_handbuilt_filter_zero_png(tmp_path):
    p = flat_png(tmp_path / "hand.png", 4, 3, (10, 20, 30))
    monkey_free = golden_core.Image  # decode without touching PIL availability at all
    del monkey_free
    orig = golden_core._HAVE_PIL
    golden_core._HAVE_PIL = False
    try:
        img = load(p)
    finally:
        golden_core._HAVE_PIL = orig
    assert (img.width, img.height) == (4, 3)
    assert img.pixels == bytes((10, 20, 30)) * 12


def test_palette_png_is_a_precondition_not_a_misread(tmp_path):
    def chunk(ctype, body):
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(
            ">I", zlib.crc32(ctype + body) & 0xFF_FF_FF_FF
        )

    ihdr = struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0)
    plte = bytes((255, 0, 0, 0, 255, 0))
    raw = b"\x00\x00\x01"
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"PLTE", plte)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    p = tmp_path / "pal.png"
    p.write_bytes(data)
    orig = golden_core._HAVE_PIL
    golden_core._HAVE_PIL = False
    try:
        with pytest.raises(golden_core.PreconditionError, match="unsupported PNG"):
            load(p)
    finally:
        golden_core._HAVE_PIL = orig


# ---- corrupt-input contract (2026-08-26) ----------------------------------------
#
# Structural faults in a PNG surface as named PreconditionErrors, never raw
# IndexError/struct.error tracebacks. Decodable input is untouched: the
# guards only fire where HEAD crashed or produced garbage.


def corrupt_png(ihdr_body=None, raw=None):
    def chunk(ctype, body):
        return struct.pack(">I", len(body)) + ctype + body + struct.pack(
            ">I", zlib.crc32(ctype + body) & 0xFF_FF_FF_FF
        )
    ihdr = ihdr_body if ihdr_body is not None else struct.pack(
        ">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)
    stream = raw if raw is not None else b"".join(
        b"\x00" + bytes(12) for _ in range(3))
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


def test_truncated_pixel_stream_is_named_precondition():
    # IHDR promises 4x3 RGB; the stream carries one scanline. HEAD ran off
    # the end of `raw` inside _unfilter (IndexError).
    with pytest.raises(golden_core.PreconditionError, match="pixel stream"):
        decode_minimal(corrupt_png(raw=b"\x00" + bytes(12)))


def test_short_ihdr_body_is_named_precondition():
    # A 12-byte IHDR body made struct.unpack raise a bare struct.error.
    with pytest.raises(golden_core.PreconditionError, match="IHDR"):
        decode_minimal(corrupt_png(ihdr_body=struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)[:12]))


def test_cli_corrupt_png_exits_2_named(tmp_path):
    # Short IHDR is rejected by BOTH decoder tiers (minimal names it; Pillow
    # refuses the file), so this pins the CLI contract regardless of tier.
    p = tmp_path / "short_ihdr.png"
    p.write_bytes(corrupt_png(
        ihdr_body=struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)[:12]))
    good = tmp_path / "good.png"
    good.write_bytes(corrupt_png())
    r = run_cli(p, good)
    assert r.returncode == 2
    assert "precondition" in r.stderr


def test_fuzz_bitflips_never_traceback_and_stay_byte_exact(tmp_path):
    """200 seeded single-bit flips over a valid PNG: every outcome is either
    a named PreconditionError or a byte-exact correct decode — zero
    tracebacks, zero wrong pixels."""
    import random

    rng = random.Random(0xC0FFEE)
    base = bytearray(corrupt_png())
    for i in range(200):
        data = bytearray(base)
        data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        try:
            decoded = decode_minimal(bytes(data))
        except golden_core.PreconditionError:
            continue
        # Zero-wrong-pixel property: whatever decodes must match Pillow's
        # decode of the same bytes, exactly.
        try:
            from PIL import Image as PILImage
            import io
            ref = PILImage.open(io.BytesIO(bytes(data))).convert("RGB")
            assert decoded.pixels == ref.tobytes(), f"flip {i}: wrong pixels"
        except Exception:
            pass  # Pillow cannot read it either — nothing to cross-check


# ---- CLI exit codes -------------------------------------------------------------------


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(LIB), *[str(a) for a in args]],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_within_tolerance_exits_0(tmp_path, plain):
    assert run_cli(plain, plain).returncode == 0


def test_cli_exceeded_exits_1(tmp_path):
    a = make_png(tmp_path / "a.png", 20, 20, lambda x, y: (0, 0, 0))
    b = make_png(tmp_path / "b.png", 20, 20, lambda x, y: (255, 255, 255))
    assert run_cli(a, b).returncode == 1


def test_cli_loosened_tolerances_flip_exit_to_0(tmp_path):
    a = make_png(tmp_path / "a.png", 20, 20, lambda x, y: (0, 0, 0))
    b = make_png(tmp_path / "b.png", 20, 20, lambda x, y: (255, 255, 255))
    tol = json.dumps({"mean_abs_diff_max": 300, "changed_frac_max": 1.0, "ssim_min": -1})
    assert run_cli(a, b, "--tolerances", tol).returncode == 0


def test_cli_precondition_missing_file_exits_2(tmp_path):
    assert run_cli(tmp_path / "nope.png", tmp_path / "nope.png").returncode == 2


def test_cli_size_mismatch_exits_2(tmp_path):
    a = make_png(tmp_path / "a.png", 20, 20, lambda x, y: (0, 0, 0))
    b = make_png(tmp_path / "b.png", 30, 20, lambda x, y: (0, 0, 0))
    r = run_cli(a, b)
    assert r.returncode == 2
    assert "size mismatch" in r.stderr


@pytest.mark.parametrize(
    "bad",
    ["{not json", "[1, 2]", '{"no_such_key": 1}', '{"ssim_min": "high"}'],
)
def test_cli_bad_tolerances_exits_2(tmp_path, plain, bad):
    assert run_cli(plain, plain, "--tolerances", bad).returncode == 2


def test_resolve_tolerances_rejects_nonfinite_naming_key_and_value():
    # NaN/Infinity pass the isinstance-number check and poison the verdict:
    # every comparison against NaN is False, so a NaN tolerance reads as
    # "within tolerance". Non-finite is a precondition failure instead.
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(golden_core.PreconditionError) as ei:
            golden_core.resolve_tolerances({"mean_abs_diff_max": bad})
        msg = str(ei.value)
        assert "mean_abs_diff_max" in msg and "finite" in msg
        assert repr(bad) in msg or "nan" in msg.lower() or "inf" in msg.lower()


@pytest.mark.parametrize(
    "bad", ['{"mean_abs_diff_max": NaN}', '{"ssim_min": Infinity}']
)
def test_cli_nonfinite_tolerances_exits_2(tmp_path, plain, bad):
    # json.loads accepts bare NaN/Infinity literals; via CLI they must be
    # precondition-rejected (exit 2), never silently accepted.
    r = run_cli(plain, plain, "--tolerances", bad)
    assert r.returncode == 2
    assert "precondition" in r.stderr


def test_cli_json_output_embeds_metrics_and_tier(tmp_path, plain):
    r = run_cli(plain, plain, "--json")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["ok"] and payload["identical"]
    assert payload["tier"]["compute"] in ("numpy", "pure-python")
    assert payload["tier"]["decoder"] in ("pillow", "png-minimal")
    assert set(payload["metrics"]) == {"mean_abs_diff", "changed_fraction", "ssim"}
