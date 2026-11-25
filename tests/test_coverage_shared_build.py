"""The crates of ONE repo share one instrumented build dir; their reports stay their own
(GOH.COVSHAREDBUILD).

`_coverage_rust.sh` keyed the build dir on each crate's `pwd -P`, so a repo of many cargo
workspaces got one build dir PER CRATE and compiled every shared dependency cold in each: one
private server workspace push (2026-11-14 17:09-17:13) made 29 dirs in ~/.cache/goh/llvm-cov, each
rebuilding libsqlite3-sys, libc and cc, and the cache held 34 dirs, 9.3 GiB, over its 8 GiB budget
-- the machine's top file writer, fseventsd at 100%. The key is now the repo's root, so its crates
share one build dir, and different repos stay apart.

Sharing has two hazards, both pinned here with a fixture shaped like a private server workspace: two
crates that each depend on a third by path, and each with a test target of the SAME name (`version`,
which 29 a private server workspace crates have). cargo-llvm-cov's report reads every executable in
the build dir whose name is one of the workspace's targets, so a shared dir would hand crate b crate
a's `version` binary; and `_cov_prune.py` deleted such strays there, so a crate could delete a
sibling's binary mid-run. The report now reads a view of THIS build's executables only.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from _fast_git import fast_init
from _small_rust import small_builds
from conftest import REPO_ROOT, hermetic_env

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

COMMON = "pub fn base() -> u32 {\n    1\n}\n"
# a and b differ in what their tests reach, so a report that read the other's objects reads a
# different number.
LIB_A = "pub fn one() -> u32 {\n    common::base()\n}\n\npub fn unused_a() -> u32 {\n    7\n}\n"
LIB_B = (
    "pub fn two() -> u32 {\n    common::base() + 1\n}\n\n"
    "pub fn three() -> u32 {\n    3\n}\n\n"
    "pub fn unused_b() -> u32 {\n    8\n}\n"
)
VERSION_A = "#[test]\nfn version() {\n    assert_eq!(a::one(), 1);\n}\n"
VERSION_B = "#[test]\nfn version() {\n    assert_eq!(b::two() + b::three(), 5);\n}\n"


# What cargo writes for a path-only workspace of its own, so no crate spawns it to be told.
LOCK = """\
version = 4

[[package]]
name = "{name}"
version = "0.1.0"
{deps}"""
COMMON_DEP = 'dependencies = [\n "common",\n]\n\n[[package]]\nname = "common"\nversion = "0.1.0"\n'


def _crate(root: Path, name: str, lib: str, test: str | None, deps: str = "") -> Path:
    d = root / "crates" / name
    (d / "src").mkdir(parents=True)
    (d / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n\n'
        f"[dependencies]\n{deps}"
    )
    (d / "src" / "lib.rs").write_text(lib)
    if test is not None:
        (d / "tests").mkdir()
        (d / "tests" / "version.rs").write_text(test)
    # A build script of its own, as most a private server workspace crates have: every build
    # script's executable is named `build_script_build`, and the report must count none of them.
    (d / "build.rs").write_text(
        'fn main() {\n    println!("cargo:rerun-if-changed=build.rs");\n}\n'
    )
    (d / "Cargo.lock").write_text(LOCK.format(name=name, deps=COMMON_DEP if deps else ""))
    return d


def _repo(root: Path) -> tuple[Path, Path]:
    fast_init(root)
    small_builds(root)
    _crate(root, "common", COMMON, None)
    dep = 'common = { path = "../common" }\n'
    return _crate(root, "a", LIB_A, VERSION_A, dep), _crate(root, "b", LIB_B, VERSION_B, dep)


def _cov(crate: Path, cache: Path) -> tuple[str, str, list[str]]:
    """(the summary line, the workspace part's build log, the source files its lcov names)."""
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
         "--floor", "0", str(crate)],
        cwd=crate, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
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
    return line.split(" (floor")[0][-40:], log, files


def _builds(cache: Path) -> list[str]:
    return sorted(p.name for p in cache.iterdir() if p.is_dir() and not p.name.startswith("."))


# Each crate's own report: 1 of LIB_A's 2 functions is reached, 2 of LIB_B's 3 (and no test file or
# build script is counted). The first test pins them to a build dir that held that crate alone.
REPORT = {
    "a": "↔ [coverage] 50.0% of coverable lines",
    "b": "↔ [coverage] 66.67% of coverable lines",
}


def test_the_crates_of_one_repo_share_one_build_and_keep_their_own_reports(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    a, b = _repo(tmp_path / "repo")
    got_a, _log, files_a = _cov(a, cache)  # the repo's first build: a alone
    _, other_b = _repo(tmp_path / "other")  # b alone: another repo has a build dir of its own
    alone_b = _cov(other_b, cache)[0]
    assert len(_builds(cache)) == 2, f"two repos, {_builds(cache)}"
    assert {"a": got_a, "b": alone_b} == REPORT
    got_b, log_b, files_b = _cov(b, cache)
    assert len(_builds(cache)) == 2, f"one repo, one build dir: {_builds(cache)}"
    assert "Compiling common" not in log_b, f"b rebuilt the dependency a already built:\n{log_b}"
    assert got_b == alone_b, "a report read a sibling crate's objects"
    assert files_a and all(f.startswith(str(a.resolve())) for f in files_a), files_a
    assert files_b and all(f.startswith(str(b.resolve())) for f in files_b), files_b
    assert not [f for f in files_a + files_b if f.endswith("build.rs")], "a build script counted"


@pytest.mark.load_sensitive
def test_crates_of_one_repo_run_side_by_side_on_the_shared_build(tmp_path: Path) -> None:
    """--each-crate runs four crates at once: one crate's run never deletes or reads a sibling's
    binaries, whatever the interleaving. The second round is each crate again after the other (every
    unit fresh now): a fresh build script is still not counted."""
    a, b = _repo(tmp_path / "repo")
    cache = tmp_path / "cache"
    for _round in range(2):
        got: dict[str, tuple[str, str, list[str]]] = {}
        errors: list[BaseException] = []

        def run(name: str, crate: Path, sink: dict = got, errors: list = errors) -> None:
            try:
                sink[name] = _cov(crate, cache)
            except BaseException as exc:  # noqa: BLE001 -- re-raised on the test's thread
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(n, c)) for n, c in (("a", a), ("b", b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert {n: said for n, (said, _, _) in got.items()} == REPORT
        assert not [f for _, _, files in got.values() for f in files if f.endswith("build.rs")]
    assert len(_builds(cache)) == 1, _builds(cache)
