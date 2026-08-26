#!/usr/bin/env python3
"""golden_core — the pure pixel-comparison core shared by every repo's golden harness.

WHAT IT IS. Load two images, compute three metrics, return a verdict against
explicit tolerances. No app launches here, no baselines, no blessing — the
harness in each repo owns rendering and baseline policy; this owns only the
arithmetic, so ten harnesses cannot drift into ten slightly-different diffs.

METRICS (semantics matched to the ancestors they came from):

    mean_abs_diff     mean absolute per-channel difference, 0-255 scale.
                      Whole-frame (ZeroThunder's golden.py diffs CONTENT-relative;
                      see its SCENES table — a mostly-background frame needs its
                      content mask instead. Provided here as the base metric.)
    changed_fraction  fraction of pixels where ANY channel differs by more than
                      `changed_px_threshold`. ZoneTilerWM's ui_regression_sweep.py
                      found a MEAN delta buries a real retint (0.00 against a
                      2000x3000 pane) — counting changed pixels sees it.
    ssim              global structural similarity on luma, exactly ZeroThunder's
                      dependency-free implementation (tests/e2e/golden.py::_ssim).

TOLERANCE DEFAULTS — starting points inherited from the ancestors; a repo must
CALIBRATE on its own measured noise floor before trusting any of them:

    changed_px_threshold = 16.0   ZoneTilerWM CHANNEL_THRESHOLD: antialiasing
                                  shifts a channel a few units; recolour/move
                                  moves tens.
    changed_frac_max     = 0.08   ZeroThunder garden scene tol_count (measured
                                  noise floor x safety margin; scenes span
                                  0.05-0.22 — calibrate per scene).
    mean_abs_diff_max    = 8.0    ZeroThunder modal content tolerance
                                  (scenes span 6.0-11.0).
    ssim_min             = 0.985  ZeroThunder SSIM_MIN perceptual floor. Set 0
                                  in --tolerances to report-only.

THE BLESSING SPLIT. There is deliberately NO --update here. Re-blessing writes
baselines, which is REPO POLICY (what to bless, where baselines live, when a
blessing needs review) — ZeroThunder keeps it in tests/e2e/golden.py, ZoneTilerWM
in ui_regression_sweep.py --record. This module only answers "do these two
images match within tolerance?".

DEPENDENCY POSTURE. Ancestors hard-require GUI-adjacent stacks (ZeroThunder:
numpy + opencv; ZoneTilerWM: Pillow + numpy). This module degrades gracefully,
in two independent tiers, and says which tier ran:

    decode:  Pillow if importable, else the built-in minimal PNG decoder
             (8-bit, non-interlaced, truecolor/grayscale/+alpha; palette PNGs
             refused as PRECONDITION failure, not silently misread). Alpha is
             DROPPED, matching Pillow's convert("RGB").
    compute: numpy if importable, else pure-Python equivalents with identical
             semantics (slower on large frames; same numbers).

CLI (this module IS the CLI — `python3 lib/golden_core.py`):
    golden_core.py A B [--tolerances '{"mean_abs_diff_max": 5}'] [--json]

Exit codes: 0 within tolerance - 1 exceeded - 2 precondition (unreadable
image, size mismatch, malformed tolerances). --json prints one object with
`tier`, metrics, failures, ok — for harness embedding.

IDENTICAL FAST PATH: byte-equal images short-circuit to an exact verdict
(sha-style, as ZeroThunder does) without running metric arithmetic.
"""

import json
import math
import struct
import sys
import zlib
from collections import namedtuple

# ---- dependency tiers -------------------------------------------------------

try:
    from PIL import Image as _PILImage

    _HAVE_PIL = True
except ImportError:  # pragma: no cover - exercised via monkeypatch in tests
    _PILImage = None
    _HAVE_PIL = False

try:
    import numpy as _np

    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    _np = None
    _HAVE_NUMPY = False


class PreconditionError(Exception):
    """A comparison cannot even be attempted (unreadable/mismatched input)."""


Image = namedtuple("Image", ["width", "height", "pixels"])  # pixels: bytes, RGB row-major

# ---- loading ----------------------------------------------------------------


def load_rgb(path):
    """Load an image as an RGB `Image`, Pillow if available else the minimal PNG decoder."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        raise PreconditionError(f"cannot read {path}: {exc}") from exc
    if _HAVE_PIL:
        return _from_pil(data, path)
    return _decode_png_minimal(data, path)


def _from_pil(data, path):
    try:
        img = _PILImage.open(path)
        img.load()
    except Exception as exc:
        raise PreconditionError(f"Pillow cannot decode {path}: {exc}") from exc
    rgb = img.convert("RGB")
    return Image(rgb.width, rgb.height, rgb.tobytes())


# ---- minimal PNG decoder (fallback tier) ------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# colour type -> channels (8-bit depth only)
_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _chunk(data, pos):
    length = struct.unpack(">I", data[pos : pos + 4])[0]
    ctype = data[pos + 4 : pos + 8]
    body = data[pos + 8 : pos + 8 + length]
    return ctype, body, pos + 12 + length  # skip CRC


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, width, height, bpp):
    stride = width * bpp
    out = bytearray(height * stride)
    pos, prev = 0, bytearray(stride)
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upleft = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], upleft)) & 0xFF
        elif ftype != 0:
            raise PreconditionError(f"unsupported PNG filter type {ftype}")
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return out


def _decode_png_minimal(data, path):
    """Decode 8-bit non-interlaced grayscale / RGB / GA / RGBA PNG to RGB bytes.

    Alpha is dropped (not composited), matching Pillow's convert("RGB"). Palette
    and 16-bit PNGs are refused loudly — a silent wrong decode would poison every
    downstream number.

    Corrupt-input contract (2026-08-26): every structural fault — truncated
    chunk walk, short IHDR, pixel stream shorter than the header promises —
    surfaces as a PreconditionError NAMING THE STAGE, never a raw IndexError /
    struct.error / zlib.error traceback out of the decoder.
    """
    if not data.startswith(_PNG_SIG):
        raise PreconditionError(f"{path}: not a PNG (and Pillow is unavailable)")
    pos, idat, header = 8, [], None
    try:
        while pos + 8 <= len(data):
            ctype, body, pos = _chunk(data, pos)
            if ctype == b"IHDR":
                if len(body) != 13:
                    raise PreconditionError(
                        f"{path}: corrupt PNG IHDR ({len(body)} bytes, "
                        f"expected 13)")
                header = struct.unpack(">IIBBBBB", body)
            elif ctype == b"IDAT":
                idat.append(body)
            elif ctype == b"IEND":
                break
        if header is None or not idat:
            raise PreconditionError(f"{path}: PNG missing IHDR or IDAT")
    except PreconditionError:
        raise
    except (IndexError, struct.error) as exc:
        raise PreconditionError(
            f"{path}: corrupt PNG chunk structure: {exc}") from exc
    width, height, depth, color, comp, filt, interlace = header
    if depth != 8 or color not in _CHANNELS or comp != 0 or filt != 0 or interlace != 0:
        raise PreconditionError(
            f"{path}: unsupported PNG ({depth}-bit, colour {color}, interlace "
            f"{interlace}) — minimal decoder handles 8-bit non-interlaced "
            "grayscale/RGB/GA/RGBA only"
        )
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise PreconditionError(f"{path}: corrupt PNG stream: {exc}") from exc
    ch = _CHANNELS[color]
    stride = width * ch
    # Length precheck BEFORE unfiltering: a stream shorter than one filter
    # byte + stride per row would otherwise run off its end mid-unfilter.
    if len(raw) < height * (stride + 1):
        raise PreconditionError(
            f"{path}: corrupt PNG pixel stream: {len(raw)} bytes for "
            f"{width}x{height}x{ch} (need {height * (stride + 1)})")
    try:
        flat = _unfilter(raw, width, height, ch)
    except (IndexError, struct.error) as exc:
        raise PreconditionError(
            f"{path}: corrupt PNG during defiltering: {exc}") from exc
    n = width * height
    rgb = bytearray(n * 3)
    if color == 2:
        rgb[:] = flat
    elif color == 6:
        for i in range(n):
            rgb[i * 3 : i * 3 + 3] = flat[i * 4 : i * 4 + 3]
    elif color == 0:
        for i in range(n):
            g = flat[i]
            rgb[i * 3 : i * 3 + 3] = bytes((g, g, g))
    else:  # GA
        for i in range(n):
            g = flat[i * 2]
            rgb[i * 3 : i * 3 + 3] = bytes((g, g, g))
    return Image(width, height, bytes(rgb))


# ---- metrics ----------------------------------------------------------------


def _arrays(a, b):
    """(a3d, b3d) reshaped to (-1, 3) int arrays when numpy is available."""
    na = _np.frombuffer(a.pixels, dtype=_np.uint8).reshape(-1, 3).astype(_np.int64)
    nb = _np.frombuffer(b.pixels, dtype=_np.uint8).reshape(-1, 3).astype(_np.int64)
    return na, nb


def mean_abs_diff(a, b):
    """Mean absolute per-channel difference (0-255)."""
    if a.pixels == b.pixels:
        return 0.0
    if _HAVE_NUMPY:
        na, nb = _arrays(a, b)
        return float(_np.abs(na - nb).mean())
    total = len(a.pixels)
    s = 0
    for i in range(total):
        d = a.pixels[i] - b.pixels[i]
        s += d if d >= 0 else -d
    return s / total


def changed_fraction(a, b, threshold):
    """Fraction of pixels where ANY channel differs by more than `threshold`."""
    if a.pixels == b.pixels:
        return 0.0
    n = a.width * a.height
    if _HAVE_NUMPY:
        na, nb = _arrays(a, b)
        return float((_np.abs(na - nb).max(axis=1) > threshold).sum()) / n
    changed = 0
    px = range(0, len(a.pixels), 3)
    for i in px:
        r, g, bl = (
            abs(a.pixels[i] - b.pixels[i]),
            abs(a.pixels[i + 1] - b.pixels[i + 1]),
            abs(a.pixels[i + 2] - b.pixels[i + 2]),
        )
        if r > threshold or g > threshold or bl > threshold:
            changed += 1
    return changed / n


def ssim(a, b):
    """Global SSIM on luma — verbatim semantics of ZeroThunder's _ssim."""
    n = a.width * a.height
    if a.pixels == b.pixels:
        return 1.0
    if _HAVE_NUMPY:
        na, nb = _arrays(a, b)
        x = na.mean(axis=1)
        y = nb.mean(axis=1)
        mx, my = float(x.mean()), float(y.mean())
        vx, vy = float(x.var()), float(y.var())
        cov = float(((x - mx) * (y - my)).mean())
    else:
        lx = [0.0] * n
        ly = [0.0] * n
        for i in range(n):
            j = i * 3
            lx[i] = (a.pixels[j] + a.pixels[j + 1] + a.pixels[j + 2]) / 3
            ly[i] = (b.pixels[j] + b.pixels[j + 1] + b.pixels[j + 2]) / 3
        mx = sum(lx) / n
        my = sum(ly) / n
        vx = sum((v - mx) ** 2 for v in lx) / n
        vy = sum((v - my) ** 2 for v in ly) / n
        cov = sum((lx[i] - mx) * (ly[i] - my) for i in range(n)) / n
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    return float(
        ((2 * mx * my + c1) * (2 * cov + c2))
        / ((mx * mx + my * my + c1) * (vx + vy + c2))
    )


# ---- verdict ----------------------------------------------------------------

TOLERANCE_DEFAULTS = {
    "mean_abs_diff_max": 8.0,
    "changed_frac_max": 0.08,
    "changed_px_threshold": 16.0,
    "ssim_min": 0.985,
}


def resolve_tolerances(overrides=None):
    """Defaults overridden by `overrides`; unknown keys are a precondition error.

    Non-finite numbers (NaN, ±Inf) are rejected too: every comparison against
    NaN is False, so a NaN tolerance would silently read as "within
    tolerance" — the one verdict it must never be able to forge."""
    out = dict(TOLERANCE_DEFAULTS)
    for key, val in (overrides or {}).items():
        if key not in out:
            raise PreconditionError(f"unknown tolerance key: {key} (known: {sorted(out)})")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise PreconditionError(f"tolerance {key} must be a number, got {val!r}")
        if isinstance(val, float) and not math.isfinite(val):
            raise PreconditionError(
                f"tolerance {key} must be finite, got {val!r} "
                f"(NaN/Infinity cannot be compared against)")
        out[key] = float(val)
    return out


def compare(a, b, tolerances=None):
    """Full verdict dict: metrics, per-tolerance failures, ok."""
    tol = resolve_tolerances(tolerances)
    if (a.width, a.height) != (b.width, b.height):
        raise PreconditionError(
            f"size mismatch: {a.width}x{a.height} vs {b.width}x{b.height}"
        )
    identical = a.pixels == b.pixels
    metrics = {
        "mean_abs_diff": 0.0 if identical else mean_abs_diff(a, b),
        "changed_fraction": 0.0 if identical else changed_fraction(a, b, tol["changed_px_threshold"]),
        "ssim": 1.0 if identical else ssim(a, b),
    }
    failures = []
    if metrics["mean_abs_diff"] > tol["mean_abs_diff_max"]:
        failures.append(
            f"mean_abs_diff {metrics['mean_abs_diff']:.4f} > {tol['mean_abs_diff_max']}"
        )
    if metrics["changed_fraction"] > tol["changed_frac_max"]:
        failures.append(
            f"changed_fraction {metrics['changed_fraction']:.6f} > {tol['changed_frac_max']}"
        )
    if metrics["ssim"] < tol["ssim_min"]:
        failures.append(f"ssim {metrics['ssim']:.4f} < {tol['ssim_min']}")
    return {
        "identical": identical,
        "size": [a.width, a.height],
        "metrics": metrics,
        "tolerances": tol,
        "failures": failures,
        "ok": not failures,
        "tier": {"decoder": "pillow" if _HAVE_PIL else "png-minimal",
                 "compute": "numpy" if _HAVE_NUMPY else "pure-python"},
    }


# ---- CLI --------------------------------------------------------------------


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description="Golden-image diff: exit 0 within tolerance, 1 exceeded, 2 precondition.")
    ap.add_argument("a", help="candidate image")
    ap.add_argument("b", help="baseline image")
    ap.add_argument("--tolerances", default="{}", help="JSON overrides of the defaults")
    ap.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    args = ap.parse_args(argv)

    def pre(msg):
        print(msg, file=sys.stderr)
        return 2

    try:
        overrides = json.loads(args.tolerances)
        if not isinstance(overrides, dict):
            raise ValueError("must be a JSON object")
        img_a, img_b = load_rgb(args.a), load_rgb(args.b)
        result = compare(img_a, img_b, overrides)
    except PreconditionError as exc:
        return pre(f"✗ precondition: {exc}")
    except json.JSONDecodeError as exc:
        return pre(f"✗ precondition: --tolerances is not valid JSON: {exc}")
    except ValueError as exc:
        return pre(f"✗ precondition: --tolerances {exc}")

    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        m = result["metrics"]
        state = "identical" if result["identical"] else ", ".join(result["failures"]) or "within tolerance"
        print(
            f"{'→' if result['ok'] else '✗'} mean_abs_diff={m['mean_abs_diff']:.4f} "
            f"changed_frac={m['changed_fraction']:.6f} ssim={m['ssim']:.4f} "
            f"[{result['tier']['decoder']}/{result['tier']['compute']}] {state}"
        )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
