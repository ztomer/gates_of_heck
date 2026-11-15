"""No coverage scratch outlives its run, and the byte budget is enforced while a run is live
(EST.FSEVENTSCHURN).

fseventsd at 100% (2026-11-14) was goh's coverage gates writing instrumented builds. A run left two
kinds of scratch in the crate's `target/llvm-cov` after it ended: every test process's `.profraw`
(cleared only by the NEXT run) and `cov-view`, hard links to the shared build's executables that
keep their bytes on disk after the cache evicts the build, where no budget sees them. A push
worktree of goh held 109 MiB of profiles and 82 MiB of view (2026-11-15). Both go when the run
ends, pass or fail; the run's record (`lcov-parts`: the parts and their logs) stays.

The budget was checked when a run started and when it ended normally: the build a run makes
between the two, and every run that failed, went unchecked.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode,
    reason="cargo llvm-cov not available",
)


def _crate(root: Path, passing: bool) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "proj"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    want = 1 if passing else 2
    (root / "src" / "lib.rs").write_text(
        "pub fn one() -> u32 {\n    1\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n"
        f"    fn one() {{\n        assert_eq!(super::one(), {want});\n    }}\n}}\n"
    )
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=root, check=True, stdin=subprocess.DEVNULL
    )
    return root


def _idle(cache: Path, mib: int) -> Path:
    d = cache / "0123456789abcdef"
    d.mkdir(parents=True)
    (d / "blob").write_bytes(b"\1" * (mib << 20))
    (d / ".goh-used").write_text("")
    then = time.time() - 3600
    import os

    os.utime(d / ".goh-used", (then, then))
    return d


def _gate(crate: Path, cache: Path, budget_gib: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "gates" / "coverage_gate.sh"),
            "--lang",
            "rust",
            "--floor",
            "0",
            str(crate),
        ],
        cwd=crate,
        capture_output=True,
        text=True,
        timeout=patience(600),
        stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_COV_CACHE=str(cache), GOH_COV_CACHE_MAX_GIB=budget_gib),
        check=False,
    )


def _scratch(crate: Path) -> list[str]:
    tgt = crate / "target" / "llvm-cov"
    return sorted(
        str(p.relative_to(crate)) for p in [*tgt.glob("*.profraw"), *tgt.glob("cov-view")]
    )


@pytest.mark.parametrize("passing", [True, False], ids=["pass", "fail"])
def test_no_coverage_scratch_outlives_its_run(tmp_path, passing) -> None:
    crate = _crate(tmp_path / "proj", passing)
    r = _gate(crate, tmp_path / "cache", "0")
    assert r.returncode == (0 if passing else 1), r.stdout + r.stderr
    assert _scratch(crate) == [], "the run left its scratch behind"
    assert (crate / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info.log").is_file()


def test_the_budget_holds_against_the_build_a_failing_run_made(tmp_path) -> None:
    """The idle entry fits the budget when the run starts; the run's own build pushes the cache
    past it, and the run fails. The idle entry must go anyway."""
    cache = tmp_path / "cache"
    idle = _idle(cache, 2)
    crate = _crate(tmp_path / "proj", passing=False)
    r = _gate(
        crate, cache, "0.0025"
    )  # 2.5 MiB: the idle 2 MiB fits; it and the 1.2 MiB build do not
    assert r.returncode == 1, r.stdout + r.stderr
    assert not idle.exists(), "a failing run's build pushed the cache past its budget unchecked"


def test_the_budget_is_enforced_while_the_run_is_live(tmp_path) -> None:
    """Not only when the run ends: the external command runs mid-run, after the workspace's build,
    and by then the idle entry that build pushed past the budget is already gone."""
    cache = tmp_path / "cache"
    idle = _idle(cache, 2)
    crate = _crate(tmp_path / "proj", passing=True)
    (crate / "src" / "main.rs").write_text('fn main() {\n    println!("{}", proj::one());\n}\n')
    seen = tmp_path / "seen"
    r = subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "gates" / "coverage_gate.sh"),
            "--lang",
            "rust",
            "--floor",
            "0",
            "--external",
            (
                f'"$GOH_COVERAGE_BIN_DIR/proj" >/dev/null; if [ -d "{idle}" ]; then echo present; '
                f'else echo gone; fi >"{seen}"'
            ),
            str(crate),
        ],
        cwd=crate,
        capture_output=True,
        text=True,
        timeout=patience(600),
        stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_COV_CACHE=str(cache), GOH_COV_CACHE_MAX_GIB="0.0025"),
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text().strip() == "gone", "the budget waited for the run to end"
