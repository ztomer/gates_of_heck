"""rust_gate.sh — parity proof of the un-forked rewrite against the legacy
fork (tests/fixtures/rust_gate_legacy.sh), on REAL crates.

A2 (parity proof before cutover): old and new must agree on the exit-code
class for every scenario — clean, dirty fmt, clippy warning, #[allow]
present, and checker-absent. Output wording may differ (the new gate uses
the shared _common.sh contract); behavior may not.

G2f: the PLUMBING tests -- exit class, that the tool's own output reaches the user, what runs where,
the lockfile and coverage-mode refusals -- ride the recorded fake cargo (tests/_fake_cargo.py), with
a RED answer planted where the scenario is a failing tool. What the gate does with a tool's answer
does not depend on a compile, and 25 s of real clippy over one-line crates was the file's whole
cost. Two things stay on REAL cargo, on purpose: `test_real_cargo_end_to_end` (the one red-and-green
proof of this gate, which also checks the planted text against what the real tools print, so the
recorded answers cannot drift) and the coverage pair (llvm-cov numbers have no recording).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from _fast_git import fast_init  # noqa: E402
import _fake_cargo  # noqa: E402

LEGACY = REPO_ROOT / "tests" / "fixtures" / "rust_gate_legacy.sh"
CURRENT = REPO_ROOT / "gates" / "rust_gate.sh"
CHECKER = REPO_ROOT / "checks" / "check_no_allow.py"

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")

CARGO_TOML = '[package]\nname = "parity"\nversion = "0.1.0"\nedition = "2021"\n'
LIB_CLEAN = "pub fn add(a: u64, b: u64) -> u64 {\n    a + b\n}\n"
LIB_DIRTY = "pub fn add(a: u64,b: u64) -> u64 {\n    a + b  }\n"
# rustfmt-clean, so a red verdict is CLIPPY's (let_and_return): the one-line spelling this used to be
# failed `cargo fmt` first, and the "clippy" tests were fmt tests that passed for the wrong reason.
LIB_WARN = "pub fn f() -> u64 {\n    let x = 1u64;\n    x\n}\n"
LIB_ALLOW = "#[allow(dead_code)]\npub fn dead() {}\n"
LIB_TESTED = (
    "pub fn add(a: u64, b: u64) -> u64 {\n    a + b\n}\n"
    "\n"
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    use super::*;\n"
    "\n"
    "    #[test]\n"
    "    fn adds() {\n"
    "        assert_eq!(add(1, 2), 3);\n"
    "    }\n"
    "}\n"
)
LIB_UNTESTED = "pub fn untested() -> u64 {\n    42\n}\n"  # pub: no dead-code lint, 0% coverage

# What the real tools printed for LIB_DIRTY / LIB_WARN (rustc 1.99, 2026-10-26), planted into the fake
# for the scenarios below. The MARKERS are what the tests read; `test_real_cargo_end_to_end` asserts
# the real tools still print them.
FMT_MARKER, CLIPPY_MARKER = "Diff in", "warning"
FMT_RED = "Diff in /tmp/parity/src/lib.rs:1:\n-pub fn add(a: u64,b: u64) -> u64 {\n+pub fn add(a: u64, b: u64) -> u64 {\n"
CLIPPY_RED = (
    "error: returning the result of a `let` binding from a block\n"
    " --> src/lib.rs:1:35\n"
    "error: could not compile `parity` (lib) due to 1 previous error\n"
    "warning: build failed, waiting for other jobs to finish...\n"
)


def _git(crate: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=crate, capture_output=True, check=True, stdin=subprocess.DEVNULL
    )


def mkcrate(tmp: Path) -> Path:
    # A git repo, like every repo the gate runs on: the house no-#[allow]
    # checker scans TRACKED Rust files, so a bare directory is not a subject.
    (tmp / "src").mkdir(parents=True)
    (tmp / "Cargo.toml").write_text(CARGO_TOML)
    (tmp / "src" / "lib.rs").write_text("")  # a target, or cargo has no package to lock
    # A TRACKED lockfile, like every Rust repo in the estate: the gate treats it as an input.
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"],
        cwd=tmp,
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    fast_init(tmp)
    _git(tmp, "add", "Cargo.lock")
    _git(
        tmp,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    return tmp


def write_lib(c: Path, body: str) -> None:
    (c / "src" / "lib.rs").write_text(body)
    _git(c, "add", "-A")


def run_script(script: Path, crate: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(script), str(crate)],
        cwd=crate,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture(scope="module")
def warm_crate(tmp_path_factory):
    """One compiled crate so clippy's dependency build doesn't dominate."""
    c = mkcrate(tmp_path_factory.mktemp("warm"))
    write_lib(c, LIB_CLEAN)
    r = subprocess.run(
        ["cargo", "build", "-q"], cwd=c, capture_output=True, stdin=subprocess.DEVNULL
    )
    assert r.returncode == 0, r.stderr.decode()[:500]
    return c


def clone(warm: Path, dest: Path) -> Path:
    c = mkcrate(dest)
    shutil.rmtree(c / "src")
    shutil.copytree(warm / "src", c / "src")
    _git(c, "add", "-A")
    return c


@pytest.fixture
def fake(tmp_path, monkeypatch) -> Path:
    """The recorded fake cargo first on PATH; returns the directory `_fake_cargo.plant_red` takes."""
    where = tmp_path / "fake-cargo"
    bin_dir, _ = _fake_cargo.install(where)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return where


def fresh(tmp: Path, body: str = LIB_CLEAN) -> Path:
    """A crate for a faked test: no compile, so no warm copy to clone."""
    c = mkcrate(tmp)
    write_lib(c, body)
    return c


def test_real_cargo_end_to_end(tmp_path, warm_crate):
    """THE one red-and-green proof of this gate on REAL cargo (everything else here is recorded):
    a clean crate passes both scripts, and a real clippy finding fails both. It also pins the
    recorded answers the faked tests plant: the real tools still print the markers they read."""
    c = clone(warm_crate, tmp_path / "clean")
    assert run_script(LEGACY, c).returncode == 0
    assert run_script(CURRENT, c).returncode == 0
    c = clone(warm_crate, tmp_path / "clippy")
    write_lib(c, LIB_WARN)
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode != 0 and new.returncode != 0
    assert CLIPPY_MARKER in (old.stderr + old.stdout).lower()
    assert CLIPPY_MARKER in (new.stderr + new.stdout).lower()
    # The recorded text is what a real tool says: the planted answers carry the markers the faked
    # tests read, and the real fmt (cheap: no compile) and clippy still print them.
    assert FMT_MARKER in FMT_RED and CLIPPY_MARKER in CLIPPY_RED.lower()
    write_lib(c, LIB_DIRTY)
    fmt = subprocess.run(
        ["cargo", "fmt", "--all", "--", "--check"],
        cwd=c,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    assert fmt.returncode != 0 and FMT_MARKER in fmt.stdout + fmt.stderr


def test_clean_crate_both_pass(tmp_path, fake):
    c = fresh(tmp_path / "clean")
    assert run_script(LEGACY, c).returncode == 0
    assert run_script(CURRENT, c).returncode == 0


def test_dirty_fmt_both_fail_and_show_output(tmp_path, fake):
    c = fresh(tmp_path / "fmt", LIB_DIRTY)
    _fake_cargo.plant_red(fake, "fmt", FMT_RED, code=1, stream="stdout")
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode != 0 and new.returncode != 0
    # Contract property 2 in BOTH: failing tool output is printed.
    assert FMT_MARKER in old.stderr or FMT_MARKER in old.stdout
    assert FMT_MARKER in new.stderr or FMT_MARKER in new.stdout


def test_clippy_warning_both_fail(tmp_path, fake):
    c = fresh(tmp_path / "clippy", LIB_WARN)
    _fake_cargo.plant_red(fake, "clippy", CLIPPY_RED, code=101)
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode != 0 and new.returncode != 0
    assert CLIPPY_MARKER in (old.stderr + old.stdout).lower()
    assert CLIPPY_MARKER in (new.stderr + new.stdout).lower()


def test_allow_attr_fails_when_checker_installed(tmp_path, fake):
    # LEGACY needed a repo-local copy of the checker; CURRENT runs the house
    # one regardless.
    c = fresh(tmp_path / "allow-old", LIB_ALLOW)
    (c / "tools").mkdir()
    shutil.copy(CHECKER, c / "tools" / "check_no_allow.py")
    assert run_script(LEGACY, c).returncode != 0
    c = fresh(tmp_path / "allow-new", LIB_ALLOW)
    r = run_script(CURRENT, c)
    assert r.returncode != 0
    assert "no #[allow]" in r.stdout + r.stderr


def test_no_allow_is_never_skipped_for_want_of_a_local_copy(tmp_path, fake):
    # LEGACY warned and skipped without tools/check_no_allow.py — which is how
    # every repo without a vendored copy went unchecked. CURRENT never skips.
    c = fresh(tmp_path / "nochecker")
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode == 0 and new.returncode == 0
    assert "missing" in (old.stdout + old.stderr).lower()
    assert "skipped" not in (new.stdout + new.stderr).lower()
    assert "no #[allow]" in new.stdout + new.stderr


def test_cargo_subdir_argument_supported_by_both(tmp_path, fake):
    # <repo> <cargo_dir> form: fmt/clippy run in the subdir, checker at root.
    # The git repo is the OUTER dir here (the real shape: one repo, a crate in
    # a subdir); the checker runs from the repo root and finds the crate.
    repo = tmp_path / "subdir-repo"
    repo.mkdir()
    inner = repo / "crates" / "parity"
    (inner / "src").mkdir(parents=True)
    (inner / "Cargo.toml").write_text(CARGO_TOML)
    (inner / "src" / "lib.rs").write_text(LIB_CLEAN)
    # The real shape: a TRACKED lockfile. (The legacy script's unlocked clippy used to write it, and
    # the current gate, run second, found what the first had made.)
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"],
        cwd=inner,
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    fast_init(repo)
    _git(repo, "add", "-A")
    _, log = _fake_cargo.install(fake)  # the fixture's own fake, for its call log
    for script in (LEGACY, CURRENT):
        r = subprocess.run(
            ["/bin/bash", str(script), str(repo), str(inner)],
            cwd=repo,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        assert r.returncode == 0, r.stderr
        for sub in ("fmt", "clippy"):
            where = {Path(d).resolve() for d in _fake_cargo.calls(log, sub)}
            assert where == {inner.resolve()}, f"{script.name}: cargo {sub} ran in {where}"
        log.unlink()


# ── coverage floor (current gate only; the legacy fork predates it) ──────────


def test_no_floor_warns_and_skips_coverage(tmp_path, fake):
    c = fresh(tmp_path / "nocovfloor")
    r = run_script(CURRENT, c)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no coverage floor" in (r.stdout + r.stderr).lower()


def test_floor_set_runs_coverage_green(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covgreen")
    write_lib(c, LIB_TESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=50\n")
    r = run_script(CURRENT, c)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "coverage" in (r.stdout + r.stderr).lower()


def test_floor_set_fails_when_uncovered(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covred")
    write_lib(c, LIB_UNTESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=80\n")
    r = run_script(CURRENT, c)
    assert r.returncode != 0, r.stdout + r.stderr
    combined = (r.stdout + r.stderr).lower()
    assert "coverage" in combined
    # The reason must be the floor, not a usage/config error: exit 2 is
    # argparse/exit-2 territory, and a miswired gate must not hide there.
    # (Rust mode reports "0.0% of coverable lines (floor 80%, 0/3 lines)".)
    assert "uncovered lines" in combined, r.stdout + r.stderr


# ── coverage deferred (GOH_RUST_COVERAGE=defer) ──────────────────────────────
# A commit hook gating every crate a release touches ran one cold instrumented
# build per crate (media_server, 2026-09-22: 27 crates, most of a 13-minute
# release) and the push gate ran them again. A repo whose push gate checks
# the floor defers it at commit time - by NAME, never by silence.


def test_deferred_coverage_is_named_and_not_run(tmp_path, fake):
    c = fresh(tmp_path / "covdefer", LIB_UNTESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=80\n")
    r = run_script(CURRENT, c, {"GOH_RUST_COVERAGE": "defer"})
    combined = r.stdout + r.stderr
    assert r.returncode == 0, "an uncovered crate passes only if coverage did not run: " + combined
    assert "coverage deferred" in combined.lower(), combined


def test_an_unknown_coverage_mode_fails(tmp_path, fake):
    c = fresh(tmp_path / "covmode", LIB_TESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=50\n")
    r = run_script(CURRENT, c, {"GOH_RUST_COVERAGE": "skip"})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "GOH_RUST_COVERAGE" in r.stdout + r.stderr


# ── the lockfile is an INPUT to the gate, never an OUTPUT ─────────────────────
# antiknob, 2026-10-04: without `--locked`, cargo re-resolves and REWRITES a stale `Cargo.lock`,
# then lints and builds happily -- a manifest-only dependency change passes green and the lockfile
# diff is the only trace of what moved. antiknob neutralised it locally with tools/lock_guard.sh;
# the gate every Rust repo runs is where the class closes.


def test_a_stale_lockfile_fails_the_gate_and_is_not_rewritten(tmp_path, fake):
    c = fresh(tmp_path / "stale")
    # A manifest change the lockfile does not record: the package's version moved.
    (c / "Cargo.toml").write_text(CARGO_TOML.replace('version = "0.1.0"', 'version = "0.2.0"'))
    before = (c / "Cargo.lock").read_bytes()
    got = run_script(CURRENT, c)
    assert got.returncode != 0, got.stdout + got.stderr
    assert "--locked" in got.stdout + got.stderr, got.stdout + got.stderr
    assert (c / "Cargo.lock").read_bytes() == before, "the gate rewrote the lockfile it was judging"


def test_a_missing_lockfile_is_refused_not_generated(tmp_path, fake):
    c = fresh(tmp_path / "nolock")
    _git(c, "rm", "-q", "Cargo.lock")
    got = run_script(CURRENT, c)
    assert got.returncode != 0 and "Cargo.lock" in got.stdout + got.stderr, got.stdout + got.stderr
    assert not (c / "Cargo.lock").exists(), "the gate generated the lockfile it should require"


def test_every_resolving_cargo_call_in_the_house_gates_is_locked():
    """The class, read off the source: a new cargo step without `--locked` goes red here."""
    import re

    resolving = re.compile(
        r"\bcargo (clippy|check|build|test|llvm-cov(?! (clean|show-env|report))|run|doc)\b"
    )
    missing = []
    # EVERY gate script, not a list of three: `_coverage_rust.sh` was split out of coverage_gate.sh
    # for the line cap and its cargo calls were never read here.
    gates = sorted(p for p in (REPO_ROOT / "gates").glob("*.sh") if "cargo " in p.read_text())
    assert any(p.name == "_coverage_rust.sh" for p in gates), [p.name for p in gates]
    for gate in gates:
        path = f"gates/{gate.name}"
        heredoc = None
        for n, line in enumerate(gate.read_text().splitlines(), 1):
            # A heredoc body (a usage text) is prose, not a call.
            if heredoc is not None:
                heredoc = None if line.strip() == heredoc else heredoc
                continue
            opened = re.search(r"<<-?\s*'?\"?(\w+)", line)
            if opened:
                heredoc = opened.group(1)
            # A message that NAMES a command is not a call: quoted text is dropped first.
            code = re.sub(r'"[^"]*"', '""', line.split("#", 1)[0])
            if (
                resolving.search(code)
                and "--locked" not in code
                and not code.rstrip().endswith("\\")
            ):
                missing.append(f"{path}:{n}: {line.strip()}")
    assert not missing, "cargo calls that may rewrite Cargo.lock:\n" + "\n".join(missing)
