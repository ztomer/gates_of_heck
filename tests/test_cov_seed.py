"""A new checkout of a known repo starts its coverage build from its seed (GOH.COVCACHEWARM).

The build dir of gates/_coverage_rust.sh is keyed on the checkout's path, so every new path of a
repo -- a land slot, a lane worktree -- built every dependency cold (440 s for a first Rust land
in a slot at load 140-160). gates/_cov_seed.py keeps one seed per repository (git's common dir) and
copies it into a fresh entry, minus the checkout's own packages, whose units cargo names alike in
every checkout and would read as fresh against another checkout's sources.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "gates"))
import _cov_seed

HAS_COV = (
    shutil.which("cargo") is not None
    and not subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode
)

EXT = "pub fn base() -> u32 {\n    1\n}\n"
LIB_ONE = "pub fn one() -> u32 {\n    ext::base()\n}\n\npub fn unused() -> u32 {\n    7\n}\n"
# The second checkout's source: another line count and another reach, so a report that read the
# first checkout's objects reads another number.
LIB_TWO = (
    "pub fn one() -> u32 {\n    ext::base()\n}\n\npub fn two() -> u32 {\n    2\n}\n\n"
    "pub fn unused() -> u32 {\n    7\n}\n"
)
TEST_ONE = "#[test]\nfn one() {\n    assert_eq!(a::one(), 1);\n}\n"
TEST_TWO = "#[test]\nfn both() {\n    assert_eq!(a::one() + a::two(), 3);\n}\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL,
                   env=hermetic_env(drop_git=True))  # fmt: skip


def _write(root: Path, lib: str, test: str) -> None:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "src" / "lib.rs").write_text(lib)
    (root / "tests" / "t.rs").write_text(test)


def _ext(tmp: Path) -> Path:
    """A path dependency OUTSIDE the repo: the same absolute file for every checkout, so shared."""
    d = tmp / "ext"
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text('[package]\nname = "ext"\nversion = "0.1.0"\nedition = "2021"\n')
    (d / "src" / "lib.rs").write_text(EXT)
    return d


def _repo(root: Path, ext: Path, lib: str, test: str) -> Path:
    root.mkdir(parents=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "a"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n\n'
        f'[dependencies]\next = {{ path = "{ext}" }}\n'
    )
    _write(root, lib, test)
    (root / ".gitignore").write_text("target/\n")
    subprocess.run(["cargo", "generate-lockfile", "-q"], cwd=root, check=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip
    fast_init(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one")
    return root


def _cov(crate: Path, cache: Path) -> tuple[str, str, list[str], str]:
    """(the summary, the workspace build's log, the files its lcov names, the gate's stdout)."""
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
         "--floor", "0", str(crate)],
        cwd=crate, capture_output=True, text=True, timeout=patience(600), stdin=subprocess.DEVNULL,
        env=hermetic_env(drop_git=True, GOH_COV_CACHE=str(cache)), check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    parts = crate / "target" / "llvm-cov" / "lcov-parts"
    log = (parts / "part-workspace.info.log").read_text()
    files = sorted(
        ln[3:] for ln in (parts / "part-workspace.info").read_text().splitlines()
        if ln.startswith("SF:")
    )  # fmt: skip
    line = next(ln for ln in r.stdout.splitlines() if "[coverage]" in ln)
    return line.split(" (floor")[0][-40:], log, files, r.stdout


@pytest.mark.skipif(not HAS_COV, reason="cargo llvm-cov not available")
def test_a_new_worktree_builds_only_its_own_packages_and_reports_its_own_source(
    tmp_path: Path,
) -> None:
    ext = _ext(tmp_path)
    alone = _cov(_repo(tmp_path / "alone", ext, LIB_TWO, TEST_TWO), tmp_path / "cache-alone")[0]
    main = _repo(tmp_path / "repo", ext, LIB_ONE, TEST_ONE)
    one, two = tmp_path / "wt-one", tmp_path / "wt-two"
    _git(main, "worktree", "add", "-q", "--detach", str(one))
    cache = tmp_path / "cache"
    first = _cov(one, cache)
    assert alone != first[0], "the fixture cannot tell the two checkouts' reports apart"
    seeds = [p.name for p in cache.iterdir() if p.name.startswith("seed-")]
    assert seeds == [Path(_cov_seed.seed_path(str(cache), _cov_seed.common_dir(str(one)))).name]
    # The second checkout: a new path (its own, cold, entry) and another source in its member.
    _git(main, "worktree", "add", "-q", "--detach", str(two))
    _write(two, LIB_TWO, TEST_TWO)
    got, log, files, out = _cov(two, cache)
    assert "Compiling ext" not in log, f"the new checkout built the shared dependency cold:\n{log}"
    assert "coverage seed: build from" in out, out
    assert "Compiling a " in log, f"the checkout's own package was taken from another one:\n{log}"
    assert got == alone, "the report read the other checkout's objects"
    assert files and all(f.startswith(str(two.resolve())) for f in files), files


def _fake_seed(cache: Path, src: Path, common: str) -> Path:
    seed = Path(_cov_seed.seed_path(str(cache), common))
    for part in _cov_seed.PARTS:
        units = seed / part / "debug" / "build"
        (units / "ext" / "h1" / "out").mkdir(parents=True)
        (units / "ext" / "h1" / "out" / "ext-h1.d").write_text(
            f"{seed}/{part}/debug/build/ext/h1/out/libext-h1.rlib: /outside/ext/src/lib.rs\n"
        )
        (units / "ext" / "h1" / "out" / "libext-h1.rlib").write_text("x")
        (units / "a" / "h2" / "out").mkdir(parents=True)
        (units / "a" / "h2" / "out" / "a-h2.d").write_text("x: src/lib.rs\n")
        (units / "gen" / "h3" / "out").mkdir(parents=True)
        (units / "gen" / "h3" / "out" / "gen-h3.d").write_text(f"x: {src}/gen/lib.rs\n")
    (seed / _cov_seed.SRC).write_text(f"{src}\n")
    return seed


@pytest.mark.load_sensitive
def test_two_checkouts_take_the_seed_at_once_and_each_leaves_the_checkout_packages_out(
    tmp_path: Path,
) -> None:
    main = tmp_path / "repo"
    main.mkdir()
    fast_init(main)
    (main / "f").write_text("x")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "one")
    wts = [tmp_path / "wt0", tmp_path / "wt1"]
    for wt in wts:
        _git(main, "worktree", "add", "-q", "--detach", str(wt))
    common = _cov_seed.common_dir(str(wts[0]))
    assert common == _cov_seed.common_dir(str(wts[1])) == _cov_seed.common_dir(str(main)) != ""
    cache = tmp_path / "cache"
    _fake_seed(cache, main.resolve(), common)
    lines: dict[int, str] = {}

    def take(i: int) -> None:
        lines[i] = _cov_seed.take(str(cache), f"k{i}", str(wts[i]))

    threads = [threading.Thread(target=take, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=patience(60))
    assert not [t for t in threads if t.is_alive()], "a take never finished"
    for i in range(2):
        assert "coverage seed: build+external from" in lines[i], lines
        for part in _cov_seed.PARTS:
            units = cache / f"k{i}" / part / "debug" / "build"
            assert sorted(p.name for p in units.iterdir()) == ["ext"], (i, part)
    # A warm entry takes nothing; another repository's seed is never taken.
    assert _cov_seed.take(str(cache), "k0", str(wts[0])) == ""
    other = tmp_path / "other"
    other.mkdir()
    fast_init(other)
    assert _cov_seed.take(str(cache), "k9", str(other)) == ""
    assert not (cache / "k9" / "build").exists()


def test_a_layout_it_does_not_know_is_never_taken(tmp_path: Path) -> None:
    build = tmp_path / "build"
    (build / "debug" / ".fingerprint").mkdir(parents=True)
    local, why = _cov_seed.local_packages(str(build), "/src")
    assert local == [] and "old layout" in why
