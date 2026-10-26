"""The recorded fake cargo answers green until a RED answer is planted (G2f), and a planted answer
is exactly what a gate then sees: the exit code, the recorded text on the stream it was planted on,
and nothing for a subcommand that was not planted."""

from __future__ import annotations

import subprocess
from pathlib import Path

import _fake_cargo  # noqa: E402


def _cargo(bin_dir: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(bin_dir / "cargo"), *argv],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_recorded_subcommand_is_green_until_a_red_answer_is_planted(tmp_path):
    bin_dir, log = _fake_cargo.install(tmp_path)
    assert _cargo(bin_dir, "clippy", "--locked").returncode == 0
    _fake_cargo.plant_red(tmp_path, "clippy", "error: lint\n", code=101)
    red = _cargo(bin_dir, "clippy", "--locked")
    assert (red.returncode, red.stderr, red.stdout) == (101, "error: lint\n", "")
    assert len(_fake_cargo.calls(log, "clippy")) == 2, "a planted answer is still a recorded call"


def test_a_planted_answer_belongs_to_its_subcommand_and_stream(tmp_path):
    bin_dir, _ = _fake_cargo.install(tmp_path)
    _fake_cargo.plant_red(tmp_path, "fmt", "Diff in x\n", code=1, stream="stdout")
    fmt = _cargo(bin_dir, "fmt", "--all", "--", "--check")
    assert (fmt.returncode, fmt.stdout, fmt.stderr) == (1, "Diff in x\n", "")
    for other in ("clippy", "check", "build", "test", "machete", "audit"):
        assert _cargo(bin_dir, other).returncode == 0, f"planting fmt reddened {other}"


def test_a_subcommand_that_is_not_recorded_is_the_real_cargo_and_cannot_be_planted(tmp_path):
    bin_dir, _ = _fake_cargo.install(tmp_path)
    assert _cargo(bin_dir, "-V").stdout.startswith("cargo ")
    try:
        _fake_cargo.plant_red(tmp_path, "metadata", "x")
    except AssertionError:
        return
    raise AssertionError("a metadata answer is the real cargo's; planting one must be refused")
