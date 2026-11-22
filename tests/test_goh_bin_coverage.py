"""Under coverage, a gate script's goh is the INSTRUMENTED build of its source (GOH.COVGOHBIN).

635 tests (1,322 test-s) run goh through the gate scripts' resolver (gates/_goh_bin.sh), which
answered with the by-stamp release build. So no run, not even the ship's instrumented one, stored
a profile for them, and GOH.BINREACH chose each of them for every Rust change ("no stored
profile"). The suite's coverage run names its instrumented goh (GOH_COV_GOH, .gatesrc
GOH_COV_RUST_EXTERNAL); the resolver answers with it whenever it says it was built from the source
the resolution wants, so a fixture's gate run leaves a profile like a direct run does. A binary
built from other source is never taken, and a parent's answer made without it is not inherited.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

RESOLVE = f'. "{REPO_ROOT}/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"'


def _stamp() -> str:
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "lib" / "goh_version.sh"), "--stamp", str(REPO_ROOT), "HEAD"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    assert r.stdout.strip(), r.stderr
    return r.stdout.strip()


def _instrumented(tmp_path: Path, stamp: str) -> Path:
    """A goh that says it was built from `stamp` and, run, leaves a raw profile where
    LLVM_PROFILE_FILE points, as the instrumented build does."""
    fake = tmp_path / "cov-bins" / "goh"
    fake.parent.mkdir()
    fake.write_text(
        "#!/bin/sh\n"
        f'[ "$1" = source-tree ] && echo "{stamp}"\n'
        ': > "${LLVM_PROFILE_FILE%/*}/goh-$$.profraw"\n'
    )
    fake.chmod(0o755)
    return fake


def _env(tmp_path: Path, monkeypatch, fake: Path, **extra: str) -> tuple[dict[str, str], Path]:
    prof = tmp_path / "prof"
    prof.mkdir()
    # The suite's environment: a hermetic child keeps it.
    monkeypatch.setenv("GOH_COV_GOH", str(fake))
    env = hermetic_env(**extra)
    env["LLVM_PROFILE_FILE"] = str(prof / "goh-%4m.profraw")
    return env, prof


def _run(cmd: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=patience(600), check=False)  # fmt: skip


def test_a_gate_scripts_goh_under_coverage_is_the_instrumented_build(tmp_path, monkeypatch):
    fake = _instrumented(tmp_path, _stamp())
    env, prof = _env(tmp_path, monkeypatch, fake)
    r = _run(f'{RESOLVE.rsplit(";", 1)[0]}; exec "$goh_native" emoji', env, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    profiles = [p.name for p in prof.glob("goh-*.profraw")]
    assert len(profiles) >= 2, f"the gate's run of goh left no profile: {profiles}"  # stamp + run


def test_a_parents_answer_without_it_is_not_inherited(tmp_path, monkeypatch):
    fake = _instrumented(tmp_path, _stamp())
    other = tmp_path / "release-goh"
    other.write_text("#!/bin/sh\nexit 0\n")
    other.chmod(0o755)
    key = f"{REPO_ROOT.resolve()}|live:{hermetic_env().get('GOH_LIVE', '')}"
    env, _ = _env(tmp_path, monkeypatch, fake, GOH_RESOLVED_FOR=key, GOH_RESOLVED_BIN=str(other))
    r = _run(RESOLVE, env, tmp_path)
    assert r.stdout == str(fake), r.stdout + r.stderr


@pytest.mark.parametrize("stamp", ["dirty", "some other source"])
def test_an_instrumented_build_of_other_source_is_never_taken(tmp_path, monkeypatch, stamp):
    fake = _instrumented(tmp_path, stamp)
    env, _ = _env(tmp_path, monkeypatch, fake)
    r = _run(RESOLVE, env, tmp_path)
    assert r.stdout and r.stdout != str(fake), r.stdout + r.stderr


def test_every_variable_the_coverage_run_hands_the_suite_survives_its_entry():
    """The suite drops every caller GOH_* but its declared inputs (tests/suite_home.py
    CALLER_INPUTS): GOH_COV_GOH, set in .gatesrc's external command and dropped there, left the
    gate scripts' goh the release build in the real run while the fixture test above passed."""
    import shlex

    import suite_home

    line = next(
        ln for ln in (REPO_ROOT / ".gatesrc").read_text().splitlines()
        if ln.startswith("GOH_COV_RUST_EXTERNAL=")
    )  # fmt: skip
    words = shlex.split(shlex.split(line.split("=", 1)[1])[0])
    handed = [w.split("=", 1)[0] for w in words if "=" in w and w.startswith("GOH_")]
    assert "GOH_COV_GOH" in handed, words  # the calibration: the reader sees the assignment
    assert not set(handed) - set(suite_home.CALLER_INPUTS), handed
