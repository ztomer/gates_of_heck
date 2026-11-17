"""The Rust coverage step runs its test BINARIES concurrently (GOH.COVPARBINS).

`cargo test --workspace` ran the binaries one after another, each parallel only inside itself:
monitor's push gate of 2026-11-15 (load 87 -> 40) spent 416 s of 730 in the coverage step, 197 s of
it in 82 serially run binaries -- most under a second, five of 12-31 s that serialise their own
tests behind a mutex, the other cores idle. `gates/_cov_run_tests.py` starts the built binaries
(named by the `--no-run` build's JSON) up to GOH_COV_RUST_JOBS at a time.

The instrument that can tell serial from concurrent is a RENDEZVOUS: each binary's one test
creates its own file and waits for every other binary's. Run one after another the first waits out
its patience and fails; run together all pass. The same fixture proves a binary runs as cargo runs
it (its package's root as cwd, CARGO_MANIFEST_DIR, the `.cargo/config.toml` [env] table, and in the
gate's session, never a session leader of its own), that profiles of concurrent processes still
land in the one report, that a red binary is named and never hides a second, and that
GOH_COV_RUST_JOBS=1 is the old serial run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
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

WAIT_S = 3  # the rendezvous patience: serial binaries each wait this long, then fail

LIB = "pub fn shared() -> u32 {\n    41\n}\n"

# Every binary: announce itself, wait for all of NAMES, check it runs as cargo runs it.
RENDEZVOUS = """\
use std::{{fs, path::Path, thread, time::{{Duration, Instant}}}};

extern "C" {{
    fn getsid(pid: i32) -> i32;
    fn getpid() -> i32;
}}

#[test]
fn meets_the_others() {{
    let rv = Path::new(&std::env::var("COVRV").unwrap()).to_path_buf();
    assert_eq!(cov::shared(), 41);
    let here = std::env::current_dir().unwrap().canonicalize().unwrap();
    let manifest = Path::new(&std::env::var("CARGO_MANIFEST_DIR").unwrap()).canonicalize().unwrap();
    assert_eq!(here, manifest, "a test binary runs in its package's root, as cargo runs it");
    assert_eq!(std::env::var("CARGO_PKG_NAME").unwrap(), "cov");
    assert_eq!(
        std::env::var("COV_FROM_CONFIG").as_deref(),
        Ok("from-config"),
        "a .cargo/config.toml [env] variable reaches the test, as cargo sets it"
    );
    // A session leader with no terminal that opens a tty without O_NOCTTY takes it as its own
    // controlling terminal (sys_updater's pty test, 2026-11-17): cargo never setsids a test.
    let (sid, pid) = unsafe {{ (getsid(0), getpid()) }};
    assert_ne!(sid, pid, "a test binary is no session leader: cargo test never setsids one");
    fs::write(rv.join("{me}"), "").unwrap();
    let t0 = Instant::now();
    while !{names}.iter().all(|n| rv.join(n).exists()) {{
        assert!(
            t0.elapsed() < Duration::from_secs({wait}),
            "no rendezvous: the binaries ran one after another"
        );
        thread::sleep(Duration::from_millis(20));
    }}
}}
"""

BAD = "#[test]\nfn the_seeded_failure() {\n    assert_eq!(cov::shared(), 0);\n}\n"


def _crate(root: Path, names: list[str], *, bad: bool = False) -> Path:
    fast_init(root)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "Cargo.toml").write_text(
        '[package]\nname = "cov"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (root / "src" / "lib.rs").write_text(LIB)
    quoted = "[" + ", ".join(f'"{n}"' for n in names) + "]"
    for n in names:
        (root / "tests" / f"{n}.rs").write_text(RENDEZVOUS.format(me=n, names=quoted, wait=WAIT_S))
    if bad:
        (root / "tests" / "zbad.rs").write_text(BAD)
    (root / ".cargo").mkdir()
    (root / ".cargo" / "config.toml").write_text('[env]\nCOV_FROM_CONFIG = "from-config"\n')
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=root, check=True, stdin=subprocess.DEVNULL
    )
    return root


def _gate(crate: Path, tmp_path: Path, **env: str) -> subprocess.CompletedProcess:
    rv = tmp_path / "rv"
    shutil.rmtree(rv, ignore_errors=True)
    rv.mkdir()
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
         "--floor", "0", str(crate)],
        cwd=crate, capture_output=True, text=True, timeout=patience(600), stdin=subprocess.DEVNULL,
        env=hermetic_env(
            drop_git=True, GOH_COV_CACHE=str(tmp_path / "cache"), COVRV=str(rv), **env
        ),
        check=False,
    )  # fmt: skip


def _log(crate: Path) -> str:
    return (crate / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info.log").read_text()


def test_the_test_binaries_run_concurrently_and_every_profile_lands_in_the_report(
    tmp_path: Path,
) -> None:
    crate = _crate(tmp_path / "c", ["a", "b", "c"])
    r = _gate(crate, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr + _log(crate)
    log = _log(crate)
    assert "running 4 test binaries, 4 at a time" in log, log
    lcov = (crate / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info").read_text()
    assert "DA:1,0" not in lcov and "DA:1," in lcov, lcov  # shared() was counted, not lost


def test_jobs_one_is_the_old_serial_run_and_the_rendezvous_cannot_happen(tmp_path: Path) -> None:
    crate = _crate(tmp_path / "c", ["a", "b"])
    r = _gate(crate, tmp_path, GOH_COV_RUST_JOBS="1")
    assert r.returncode != 0, "serial binaries met a rendezvous: the instrument cannot fail"
    assert "TESTS FAILED" in r.stderr and "meets_the_others" in r.stderr, r.stderr
    assert "running 3 test binaries, 1 at a time" in _log(crate)


def test_a_red_binary_fails_the_step_by_name_and_hides_no_other(tmp_path: Path) -> None:
    crate = _crate(tmp_path / "c", ["a", "b"], bad=True)
    r = _gate(crate, tmp_path)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "TESTS FAILED" in r.stderr, r.stderr
    assert "the_seeded_failure" in r.stderr.split("TESTS FAILED", 1)[1].splitlines()[0], r.stderr
    log = _log(crate)
    assert "running 4 test binaries" in log and "meets_the_others ... ok" in log, log
    assert log.rindex("the_seeded_failure ... FAILED") > log.rindex("meets_the_others ... ok")
