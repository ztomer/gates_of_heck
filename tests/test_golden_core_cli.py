"""lib/golden_core.py — the CLI contract: exit codes, --tolerances, --json.

Split from `test_golden_core.py` by concern rather than for length alone: that file is the
pixel-diff math the module IS, and this one is the process boundary around it — exit codes a CI
script branches on, the tolerances JSON a harness passes in, the report it prints, and the docs
that name it.

The exit code is the load-bearing surface: 0 within tolerance, 1 outside, 2 for a precondition the
caller must fix (a missing file, mismatched sizes, a malformed or non-finite --tolerances). A
precondition that exits 0 or 1 is a CI that fails green on a broken input, so each arm is pinned.
"""

import json
import struct

import pytest
from conftest import REPO_ROOT

from _golden_core_kit import corrupt_png, make_png, plain, run_cli  # noqa: F401


def test_cli_corrupt_png_exits_2_named(tmp_path):
    # Short IHDR is rejected by BOTH decoder tiers (minimal names it; Pillow
    # refuses the file), so this pins the CLI contract regardless of tier.
    p = tmp_path / "short_ihdr.png"
    p.write_bytes(corrupt_png(ihdr_body=struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)[:12]))
    good = tmp_path / "good.png"
    good.write_bytes(corrupt_png())
    r = run_cli(p, good)
    assert r.returncode == 2
    assert "precondition" in r.stderr


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


@pytest.mark.parametrize("bad", ['{"mean_abs_diff_max": NaN}', '{"ssim_min": Infinity}'])
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


# ── docs consistency ──────────────────────────────────────────────────────────


def test_no_phantom_diff_cli_references():
    # Regression (2026-08-26): the CHANGELOG and this module's docstring
    # referenced a phantom `golden_…diff.py` CLI that never existed as a
    # file — the CLI is golden_core.py's own __main__. No reference may come
    # back. (Needle is assembled so this scan does not flag its own source.)
    needle = "golden_" + "diff"
    offenders = []
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file() or ".git" in p.parts or "__pycache__" in p.parts:
            continue
        if p.suffix not in {".py", ".md", ".sh"}:
            continue
        if needle in p.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(p.relative_to(REPO_ROOT)))
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert offenders == [], f"phantom golden-diff references: {offenders}"


def test_golden_core_docstring_names_the_real_cli():
    text = (REPO_ROOT / "lib" / "golden_core.py").read_text()
    assert "golden_core.py A B" in text  # the CLI that actually exists
