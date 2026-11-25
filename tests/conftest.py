"""Shared fixtures: build throwaway git repos to run the checkers against.

Every disallowed glyph in this suite is built with chr(0x...) — never as a
literal — so the suite cannot trip gates_of_heck's own emoji gate.
"""

import fcntl
import json
import os
import shutil
import stat as _stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import suite_home  # (FIRST: the suite's own HOME and TMPDIR, before anything reads them)
from _tree_clone import clone_tree, template  # (no HOME/TMPDIR read at import)
from env_probe import print_safe  # (no HOME/TMPDIR read: re, shlex)
from suite_tmp import live_suite_mark, nested_root  # noqa: F401  (autouse: GOH.FLAKY2, FSCHURN4)

# THE SUITE TESTS THE TREE IT IS IN. Consumers run the gates from an export of HEAD (C4,
# gates/_from_head.sh); this suite exists to judge the change under test, so it asks for the working
# tree on purpose -- and a gate started under pytest WITHOUT it refuses rather than quietly
# judging HEAD. A test that builds its environment by dropping GOH_* must keep this one.
os.environ["GOH_LIVE"] = "1"
# ...and the refusal is THIS suite's: the marker (not GOH_-prefixed: hermetic_env keeps it) says so;
# a consumer's pytest has none. And no child starts the machine's sccache (suite_home.CACHE_TIER).
os.environ.update(GATES_OF_HECK_SUITE="1", GOH_SCCACHE_UP="off")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every test builds its OWN repos. Run from a git hook (pre-push from a linked worktree hands
# the suite GIT_DIR=<main>/.git/worktrees/<name>), an inherited GIT_DIR turns each fixture's
# `git init <tmp>` into a re-init of the REAL repository -- core.bare=true lands in the shared
# config and no checkout works (a private consumer, 2026-09-27). Dropped at import, before any fixture runs,
# using git's own list (test_hook_git_env.py); the gate's profile too (GOH.LANDGAPS).
sys.path.insert(0, str(REPO_ROOT / "checks"))
from _gitutil import local_env_vars
from host_locks import no_test_reaches_a_host_lock, scrubbed  # noqa: F401  (an autouse fixture)

# The leak check (autouse): an OWNER answers for its processes; none the session started outlive it.
from load_kit import load_reap_scope, suite_leak_check  # noqa: F401  (autouse fixtures)
from real_state_guard import no_test_writes_the_real_goh_state  # noqa: F401  (an autouse fixture)

for _name in [*local_env_vars(), *(f"GOH_TIMINGS{t}" for t in ("", "_PARENT", "_KIND", "_CWD"))]:
    os.environ.pop(_name, None)

# Disallowed-by-policy glyphs, by number (see checks/check_no_emoji.py).
EMOJI_SMILE = chr(0x1F600)  # pictograph
CHECK_MARK_BUTTON = chr(0x2705)  # dingbat check-mark-button (NOT the allowed U+2713)
VS16 = chr(0xFE0F)  # emoji variation selector
KEYCAP_COMBINE = chr(0x20E3)  # combining enclosing keycap
STAR = chr(0x2B50)

# 2026-08-24: U+21D2 (⇒ implication) and U+21C4 (⇄ exchange) moved to the
# allow-list — they are functional text operators like ↔, not decoration.
# DOUBLE_ARROW now points at U+27A1 (black rightwards arrow dingbat), still rejected.
DOUBLE_ARROW = chr(0x27A1)

# Allowed glyphs may appear literally.
ALLOWED = "→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵ ⇒ ⇄"


def pytest_configure(config):
    _drift_guard.prebuild_live(config)
    config.pluginmanager.register(_schedule, "goh-longest-first")
    config.pluginmanager.register(_land_verdicts, "goh-land-verdicts")  # GOH.LANDPREDUP
    config.pluginmanager.import_plugin("_suite_plugins")  # live share, file events: its docstring


def git(repo: Path, *args: str) -> str:
    """Run a git command in `repo`, return stdout; raise on failure."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return out.stdout


def git_ok(repo: Path, *args: str) -> bool:
    """Run a git command in `repo`; True iff it exited 0."""
    return _capture(["git", "-C", str(repo), *args], None).returncode == 0


def write(repo: Path, rel: str, content: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(__import__("_gatesrc_kit").as_fixture(rel, content), encoding="utf-8")
    return p


def stage(repo: Path, *rels: str) -> None:
    git(repo, "add", "--", *rels)


def commit_all(repo: Path, msg: str = "fixture") -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


def _build_repo_template(r: Path) -> None:
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", "# fixture\n")
    commit_all(r)


@pytest.fixture(scope="session")
def _repo_template(tmp_path_factory) -> Path:
    """`repo`'s starting state, built by git ONCE per worker (614 tests took it; 2026-10-06), through
    `template`: no auto-maintenance while it builds (GOH.TEMPLATEQUIET)."""
    return template(tmp_path_factory, "repo", _build_repo_template)


@pytest.fixture
def repo(tmp_path: Path, _repo_template: Path) -> Path:
    """An initialized git repo with one committed file: a CLONE of the template per test."""
    clone_tree(_repo_template, tmp_path / "proj")
    return tmp_path / "proj"


@pytest.fixture(scope="session", autouse=True)
def _suite_estate_state(tmp_path_factory) -> None:
    """The estate's state file, for the whole suite, in a scratch directory (BACKLOG 6.14)."""
    d = tmp_path_factory.mktemp("goh-estate-state", numbered=False)
    os.environ["GOH_ESTATE_STATE"] = str(d / "estate_audit.json")


def hermetic_env(drop_git: bool = False, **overrides: str) -> dict[str, str]:
    """A child environment with NO inherited `GOH_*` but `GOH_LIVE` (and, with `drop_git`, no
    `GIT_*`): every step's opt-in is a `GOH_*` PRESENCE test, so an inherited value adds a step the
    test never declared -- which is how `push_gate.sh`'s `set -a` reached a whole suite. ONE
    definition: eleven files carried this filter inline, and two had grown their own copy.
    `GOH_ESTATE_STATE` is KEPT: it names a file, not a step (BACKLOG 6.14)."""
    drop = ("GOH_", "GIT_") if drop_git else ("GOH_",)
    # suite_home.KEPT_GOH (GOH_LIVE, GOH_ESTATE_STATE, ...) and the machine locks' private copies
    # (host_locks.py, PRIVATE_ROOTS): a location is no opt-in; dropped, it sent every child to the
    # REAL bench lock and gate slots.
    env = scrubbed(os.environ, drop, keep=suite_home.KEPT_GOH)
    # The rust coverage build dir is keyed on a project's path (gates/_coverage_rust.sh): a fixture
    # in a fresh tmp dir would leave one never-reused build per test in the user's cache.
    env["GOH_COV_CACHE"] = _drift_guard.COV_CACHE
    # GOH_DIR is a LOCATION too, and it must be THIS checkout: without it the binary falls back to
    # ~/Projects/gates_of_heck, so a worktree's suite judged MAIN's checkers -- a new step went red
    # before landing and an edit to an old one was never tested at all (D.GOH1, 2026-10-31).
    env["GOH_DIR"] = str(REPO_ROOT)
    # No child reads the owner's seed store (GOH.SEEDPRIVATE) nor lanes the REAL load (ADAPTLANES2).
    env.update({"GOH_SEED_DIR": str(Path(tempfile.gettempdir()) / "goh-suite-no-seed-store"),
                "GOH_LANE_ADMIT": "off", **suite_home.HERMETIC_PINS, **overrides})  # fmt: skip
    return print_safe(env, ("GOH_DIR", *overrides))  # it prints redacted (GOH.PROVENENVDUMP)


# A retired checker's path -> the native check that replaced it (Phase N3, `checks/_retired.py::
# NATIVE`): `run_check` gives a suite what a consumer calling that path gets, same arguments.
import _drift_guard
import _land_verdicts
import _schedule
from _drift_guard import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401
from _goh_request import _remember_request, native_goh_path  # noqa: F401  (an autouse fixture)
from _retired import NATIVE as _NATIVE
from _tree_guard import pytest_sessionfinish, pytest_sessionstart  # noqa: F401

RETIRED = {
    f"checks/{stem}.{'sh' if check == 'shell-lint' else 'py'}": check
    for stem, check in _NATIVE.items()
}


def _capture(argv: list[str], cwd: Path | None) -> subprocess.CompletedProcess:
    """`argv` in `cwd`, output as text, nothing on stdin, the exit code left to the caller."""
    return subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )


def run_check(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a checker from this repo against `repo` (cwd=repo); a retired one runs its native."""
    argv = (
        [str(native_goh_path()), RETIRED[script], *args]
        if script in RETIRED
        else ["python3", str(REPO_ROOT / script), *args]
    )
    return _capture(argv, repo)


def run_gate(repo: Path, gate: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of this repo's gate scripts against `repo` (bash, cwd=repo)."""
    return _capture(["/bin/bash", str(REPO_ROOT / gate), *args], repo)


# ── fake swift coverage toolchain ────────────────────────────────────────────
# Shared by the coverage-gate swift tests and the coverage_swift selection
# tests. `swift`, `xcrun` and `xcodebuild` shims emit canned llvm-cov payloads
# modeled on REAL output captured from xcodebuild/llvm-cov on this machine
# (probe 2026-08-25), so the real bash gate → python helper chain is exercised
# without any Apple toolchain. Set swift_xcrun_log=... to record every xcrun
# invocation's argv (proves WHICH binary was paired with the profdata).


FAKE_EXPORT = {
    "data": [
        {
            "files": [
                {
                    "filename": "PROJ/Sources/pkg/lib.swift",
                    "summary": {"lines": {"count": 10, "covered": 5}},
                },
            ]
        }
    ],
}

# llvm-cov show format: "LINE|COUNT|source". Lines 6-10 are the uncovered half.
FAKE_SHOW = "".join(f"{n:>5}|{'  0' if n > 5 else '  7'}|line {n}\n" for n in range(1, 11))


def mk_fake_swift_toolchain(
    root: Path, second_config: bool = False, xcrun_log: Path | None = None
) -> Path:
    bin_ = root / "fakebin"
    bin_.mkdir()
    pkg = root / "proj"
    src = pkg / "Sources" / "pkg"
    src.mkdir(parents=True)
    (src / "lib.swift").write_text("\n".join(f"line {n}" for n in range(1, 11)) + "\n")

    xctest_bin = bin_ / "store" / "PkgTests.xctest" / "Contents" / "MacOS" / "PkgTests"
    xctest_bin.parent.mkdir(parents=True)
    xctest_bin.write_text("#!/bin/sh\n")
    xctest_bin.chmod(xctest_bin.stat().st_mode | _stat.S_IEXEC)
    (bin_ / "store" / "codecov").mkdir()
    (bin_ / "store" / "codecov" / "default.profdata").write_text("")

    dd = root / "dd"
    prof = dd / "Build" / "ProfileData" / "FE-DEADBEEF"
    prof.mkdir(parents=True)
    profdata = prof / "Coverage.profdata"
    profdata.write_text("")
    cfgs = ["Debug"] + (["Release"] if second_config else [])
    for cfg in cfgs:
        xb = dd / "Build" / "Products" / cfg / "AppTests.xctest" / "Contents" / "MacOS" / "AppTests"
        xb.parent.mkdir(parents=True)
        xb.write_text("#!/bin/sh\n")
        xb.chmod(xb.stat().st_mode | _stat.S_IEXEC)
    # Stale-binary pairing scenario: Debug was built LONG before the run that
    # wrote the profdata; Release (when present) is its mtime twin.
    import os as _os

    old = profdata.stat().st_mtime - 10_000
    _os.utime(dd / "Build/Products/Debug/AppTests.xctest/Contents/MacOS/AppTests", (old, old))
    if second_config:
        _os.utime(
            dd / "Build/Products/Release/AppTests.xctest/Contents/MacOS/AppTests",
            (profdata.stat().st_mtime, profdata.stat().st_mtime),
        )
    (dd / "Logs" / "Test").mkdir(parents=True)
    (dd / "Logs" / "Test" / "Test-App.xcresult").mkdir()

    export_file = root / "export.json"
    export_json = json.dumps(FAKE_EXPORT).replace("PROJ", str(src.parent.parent))
    export_file.write_text(export_json)

    show_file = root / "show.txt"
    show_file.write_text(FAKE_SHOW)

    swift = bin_ / "swift"
    swift.write_text(f"""#!/bin/bash
case "$1 $2" in
  "build --show-bin-path") echo "{bin_}/store" ;;
  *) exit 0 ;;
esac
""")
    log_line = ""
    if xcrun_log is not None:
        log_line = f'printf \'%s\\n\' "$@" >> "{xcrun_log}"'
    xcrun = bin_ / "xcrun"
    xcrun.write_text(f"""#!/bin/bash
{log_line}
sub="$1"; shift || true
case "$sub" in
  llvm-cov)
    case "$1" in
      export) cat "{export_file}" ;;
      show) cat "{show_file}" ;;
      *) exit 1 ;;
    esac ;;
  xccov) exit 1 ;;
  *) exit 1 ;;
esac
""")
    xcodebuild = bin_ / "xcodebuild"
    xcodebuild.write_text("#!/bin/bash\nexit 0\n")
    for f in (swift, xcrun, xcodebuild):
        f.chmod(f.stat().st_mode | _stat.S_IEXEC)
    return bin_


# ── xcrun --find shim ────────────────────────────────────────────────────────
# swift_gate.sh resolves the toolchain through `xcrun --find swift` BEFORE
# PATH (gates/swift_toolchain.sh). A test that shims `swift` on PATH must
# also shim xcrun to point at it, or the gate finds the real Xcode driver
# and builds the fixture repo for real.
def mk_xcrun_find_shim(bin_dir: Path, swift_path: Path) -> Path:
    xcrun = bin_dir / "xcrun"
    xcrun.write_text(
        f'#!/bin/bash\n[ "$1 $2" = "--find swift" ] && {{ echo "{swift_path}"; exit 0; }}\nexit 1\n'
    )
    xcrun.chmod(xcrun.stat().st_mode | _stat.S_IEXEC)
    return xcrun


# ── shared fake gh ───────────────────────────────────────────────────────────
# Stateful stub: `release view` succeeds iff that release was previously
# created. Shared by test_release_kit.py and test_release_hardening.py
# (was a 36-line verbatim duplicate in both — one copy now).
from _fake_gh import FAKE_GH  # noqa: F401  -- re-exported: the release kit's tests import it here


def _build_goh() -> Path:
    r = subprocess.run(
        [
            "cargo",
            "build",
            "--message-format=json",
            "-p",
            "goh",
            "--manifest-path",
            str(REPO_ROOT / "Cargo.toml"),
        ],
        capture_output=True,
        text=True,
        env=dict(os.environ, DRIFT_BUILD_OK="1"),
        stdin=subprocess.DEVNULL,  # the one build tests/_drift_guard.py allows
        check=False,
    )
    assert r.returncode == 0, f"cargo build failed:\n{r.stderr}"
    for line in r.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        target = event.get("target", {})
        if event.get("reason") == "compiler-artifact" and target.get("name") == "goh":
            path = event.get("executable")
            if path:
                return Path(path)
    raise AssertionError("goh artifact missing from cargo build output")


# Under the coverage gate's external part (GOH_COVERAGE_BIN_DIR set) or a plain recording run (its
# marks, tests/_prof_record.py `marking`), every explicit `env=` keeps LLVM_PROFILE_FILE: a test
# scrubbing its child's environment (a fake `curl` on PATH) otherwise loses the binary's runs to the
# report. Read at each call, so a suite run outside the gate is untouched (GOH.PLAINBINMARK).
_popen_init = subprocess.Popen.__init__


def _measured_popen_init(self, *args, **kwargs):
    env = kwargs.get("env")
    if (
        env is not None
        and "LLVM_PROFILE_FILE" not in env
        and (os.environ.get("GOH_COVERAGE_BIN_DIR") or _land_verdicts._prof_record.marking())
        and os.environ.get("LLVM_PROFILE_FILE")
    ):
        kwargs["env"] = {**env, "LLVM_PROFILE_FILE": os.environ["LLVM_PROFILE_FILE"]}
    _popen_init(self, *args, **kwargs)


subprocess.Popen.__init__ = _measured_popen_init


# Every `cargo build` the goh fixture runs this session, one line each. Read by
# test_goh_is_built_once_per_session: the count is the regression, not a timing.
GOH_BUILD_RECORD = "goh-builds.log"


def _shared_tmp(tmp_path_factory) -> Path:
    """The temp root every xdist worker shares (each worker's basetemp is a child of it).
    Without xdist, basetemp itself."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


@pytest.fixture(scope="session")
def goh(native_goh: Path) -> Path:
    """The native goh binary (`native_goh`, under the name most suites use)."""
    return native_goh


@pytest.fixture(scope="session")
def native_goh(tmp_path_factory) -> Path:
    """The native goh binary, built ONCE per test session and read from a private copy. A suite
    whose own `goh` fixture means something else (a gates checkout copy) asks for this name.

    "session" is per WORKER under xdist, so this fixture used to run `cargo build` once per worker:
    measured 2026-09-22, seven builds of one workspace in one session, four overlapping inside
    0.3s. Cargo re-links the uplifted target/debug/goh by remove + hardlink, so a worker copying it
    while another worker's build re-linked it hit FileNotFoundError. The pre-push gate's cold
    worktree (builds long enough to overlap widely) failed 23 parity tests at once, and a push was
    refused on a race. A per-worker copy only narrowed the window; the copy itself read the racing
    path.

    Now the first worker builds under a lock and publishes one copy atomically into the shared temp
    root; every other worker waits on the lock and reads that copy. No test reads cargo's target
    path while a build of it can be running.
    """
    shared = _shared_tmp(tmp_path_factory)
    published = shared / "goh-bin" / "goh"
    # GOH_TEST_BIN: a binary built elsewhere, used as is -- the coverage gate's INSTRUMENTED build
    # (GOH_COV_RUST_EXTERNAL), whose profiles are this suite's share of the crate's coverage. A
    # copy would still count; a rebuild here would not be instrumented.
    provided = os.environ.get("GOH_TEST_BIN", "")
    with open(shared / "goh-bin.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # released when the file closes
        if provided:
            if not os.access(provided, os.X_OK):
                raise RuntimeError(f"GOH_TEST_BIN={provided} is not an executable")
            if not (shared / GOH_BUILD_RECORD).exists():
                (shared / GOH_BUILD_RECORD).write_text("provided\n")
            published = Path(provided)
        elif not published.exists():
            built = _build_goh()
            with open(shared / GOH_BUILD_RECORD, "a") as record:
                record.write(f"{os.environ.get('PYTEST_XDIST_WORKER', 'master')}\n")
            published.parent.mkdir(exist_ok=True)
            staging = published.with_suffix(".staging")
            shutil.copy2(built, staging)
            staging.rename(published)  # atomic: readers see all of it or none
    return _land_verdicts._prof_record.calibrate(published)  # it leaves marks (GOH.PLAINBINMARK)


def run_goh(cwd: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    """`goh <args>` in `cwd`, text captured -- the native tier every suite now tests."""
    return subprocess.run(
        [str(native_goh_path()), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        # A caller's own `stdin=` or `input=` wins; otherwise the child reads nothing.
        stdin=kw.pop("stdin", None if "input" in kw else subprocess.DEVNULL),
        check=kw.pop("check", False),
        **kw,
    )


@pytest.fixture(scope="session")
def goh_build_count(tmp_path_factory, goh) -> int:
    """How many times this session built goh (after this worker's own fixture resolved)."""
    return len((_shared_tmp(tmp_path_factory) / GOH_BUILD_RECORD).read_text().splitlines())


@pytest.fixture(autouse=True, scope="session")
def _never_publish_the_live_goh():
    """No test run may replace bin/goh.

    bin/goh is every consumer repo's live structural gate. The install tests ran the real
    install.sh, which built and published it from whatever the checkout held, so a test run
    during goh development swapped every repo's gate for the work in progress (2026-09-23; the
    publish guard in scripts/build-goh.sh made that visible by refusing). Subprocesses inherit
    this; a test that passes its own `env=` builds into its own copy or sets it itself.
    """
    previous = os.environ.get("GOH_SKIP_BUILD")
    os.environ["GOH_SKIP_BUILD"] = "1"
    yield
    if previous is None:
        os.environ.pop("GOH_SKIP_BUILD", None)
    else:
        os.environ["GOH_SKIP_BUILD"] = previous


@pytest.fixture(autouse=True)
def _a_test_leaves_the_directory_it_found():
    """A test that changes this worker's directory (GOH.LANDERCWD: an in-process `lander.drain()`
    chdirs to its state dir, in a tmp dir pytest later removes) gives every later test in the worker
    a dead cwd: `git clone` exit 128, `getcwd: cannot access parent directories`."""
    try:
        start = os.getcwd()
    except OSError:
        start = ""
    yield
    if start and os.path.isdir(start):
        os.chdir(start)


def llvm_cov_shim(bin_: Path) -> None:
    """Fake `cargo-llvm-cov` (a version) and `python3`: none in Linux /usr/bin (LINUXCOVPATH)."""
    (shim := bin_ / "cargo-llvm-cov").write_text('#!/bin/bash\necho "cargo-llvm-cov 99.0.0"\n')
    shim.chmod(shim.stat().st_mode | _stat.S_IEXEC)
    if not (py := bin_ / "python3").exists():
        py.symlink_to(sys.executable)
