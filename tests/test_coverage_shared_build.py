"""The crates of ONE repo share one instrumented build dir; their reports stay their own
(GOH.COVSHAREDBUILD).

`_coverage_rust.sh` keyed the build dir on each crate's `pwd -P`, so a repo of many cargo
workspaces got one build dir PER CRATE and compiled every shared dependency cold in each: one
media_server push (2026-11-14 17:09-17:13) made 29 dirs in ~/.cache/goh/llvm-cov, each rebuilding
libsqlite3-sys, libc and cc, and the cache held 34 dirs, 9.3 GiB, over its 8 GiB budget -- the
machine's top file writer, fseventsd at 100%. The key is now the repo's root, so its crates share
one build dir, and different repos stay apart.

Sharing has two hazards, both pinned here with a fixture shaped like media_server: two crates that
each depend on a third by path, and each with a test target of the SAME name (`version`, which 29
media_server crates have). cargo-llvm-cov's report reads every executable in the build dir whose
name is one of the workspace's targets, so a shared dir would hand crate b crate a's `version`
binary; and `_cov_prune.py` deleted such strays there, so a crate could delete a sibling's binary
mid-run. The report now reads a view of THIS build's executables only.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

import pytest
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
    # A build script of its own, as most media_server crates have: every build script's executable
    # is named `build_script_build`, and the report must count none of them.
    (d / "build.rs").write_text(
        'fn main() {\n    println!("cargo:rerun-if-changed=build.rs");\n}\n'
    )
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=d, check=True, stdin=subprocess.DEVNULL
    )
    return d


def _repo(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, stdin=subprocess.DEVNULL)
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


def _alone(tmp_path: Path, name: str) -> str:
    """The crate's report from a repo and a cache of its own: the reference."""
    a, b = _repo(tmp_path / f"alone-{name}")
    return _cov(a if name == "a" else b, tmp_path / f"cache-alone-{name}")[0]


def _builds(cache: Path) -> list[str]:
    return sorted(p.name for p in cache.iterdir() if p.is_dir() and not p.name.startswith("."))


def test_the_crates_of_one_repo_share_one_build_and_keep_their_own_reports(
    tmp_path: Path,
) -> None:
    alone_a, alone_b = _alone(tmp_path, "a"), _alone(tmp_path, "b")
    assert alone_a != alone_b, "the fixture cannot tell the two reports apart"
    a, b = _repo(tmp_path / "repo")
    cache = tmp_path / "cache"
    got_a, _log, files_a = _cov(a, cache)
    got_b, log_b, files_b = _cov(b, cache)
    assert len(_builds(cache)) == 1, f"one repo, {len(_builds(cache))} build dirs: {_builds(cache)}"
    assert "Compiling common" not in log_b, f"b rebuilt the dependency a already built:\n{log_b}"
    assert (got_a, got_b) == (alone_a, alone_b), "a report read a sibling crate's objects"
    assert files_a and all(f.startswith(str(a.resolve())) for f in files_a), files_a
    assert files_b and all(f.startswith(str(b.resolve())) for f in files_b), files_b
    assert not [f for f in files_a + files_b if f.endswith("build.rs")], "a build script counted"
    # Each crate again, after the other (every unit fresh now): neither run took the other's
    # binaries away, and a fresh build script is still not counted.
    again_a, _log, again_files_a = _cov(a, cache)
    assert (again_a, again_files_a) == (alone_a, files_a)
    assert _cov(b, cache)[0] == alone_b


def test_crates_of_one_repo_run_side_by_side_on_the_shared_build(tmp_path: Path) -> None:
    """--each-crate runs four crates at once: one crate's run never deletes or reads a sibling's
    binaries, whatever the interleaving."""
    alone_a, alone_b = _alone(tmp_path, "a"), _alone(tmp_path, "b")
    a, b = _repo(tmp_path / "repo")
    cache = tmp_path / "cache"
    for _round in range(2):
        got: dict[str, str] = {}
        errors: list[BaseException] = []

        def run(name: str, crate: Path, sink: dict[str, str] = got, errors: list = errors) -> None:
            try:
                sink[name] = _cov(crate, cache)[0]
            except BaseException as exc:  # noqa: BLE001 -- re-raised on the test's thread
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(n, c)) for n, c in (("a", a), ("b", b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert got == {"a": alone_a, "b": alone_b}
    assert len(_builds(cache)) == 1, _builds(cache)


def test_two_repos_keep_two_builds(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    one, _ = _repo(tmp_path / "one")
    two, _ = _repo(tmp_path / "two")
    _cov(one, cache)
    _cov(two, cache)
    assert len(_builds(cache)) == 2, _builds(cache)
