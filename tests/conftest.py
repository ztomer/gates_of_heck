"""Shared fixtures: build throwaway git repos to run the checkers against.

Every disallowed glyph in this suite is built with chr(0x...) — never as a
literal — so the suite cannot trip gates_of_heck's own emoji gate.
"""

import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Disallowed-by-policy glyphs, by number (see checks/check_no_emoji.py).
EMOJI_SMILE = chr(0x1F600)      # pictograph
CHECK_MARK_BUTTON = chr(0x2705)  # dingbat check-mark-button (NOT the allowed U+2713)
VS16 = chr(0xFE0F)              # emoji variation selector
KEYCAP_COMBINE = chr(0x20E3)    # combining enclosing keycap
STAR = chr(0x2B50)

# 2026-08-24: U+21D2 (⇒ implication) and U+21C4 (⇄ exchange) moved to the
# allow-list — they are functional text operators like ↔, not decoration.
# DOUBLE_ARROW now points at U+27A1 (black rightwards arrow dingbat), still rejected.
DOUBLE_ARROW = chr(0x27A1)

# Allowed glyphs may appear literally.
ALLOWED = "→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵ ⇒ ⇄"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: needs a real toolchain (swiftlint); skip with -m 'not slow'",
    )


def git(repo: Path, *args: str) -> str:
    """Run a git command in `repo`, return stdout; raise on failure."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def git_ok(repo: Path, *args: str) -> bool:
    """Run a git command in `repo`; True iff it exited 0."""
    return (
        subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True
        ).returncode
        == 0
    )


def write(repo: Path, rel: str, content: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def stage(repo: Path, *rels: str) -> None:
    git(repo, "add", "--", *rels)


def commit_all(repo: Path, msg: str = "fixture") -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An initialized git repo with one committed file."""
    r = tmp_path / "proj"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "README.md", "# fixture\n")
    commit_all(r)
    return r


def run_check(repo: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """Run a checker from this repo against `repo` (cwd=repo)."""
    return subprocess.run(
        ["python3", str(REPO_ROOT / script), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def run_gate(repo: Path, gate: str, *args: str) -> subprocess.CompletedProcess:
    """Run one of this repo's gate scripts against `repo` (bash, cwd=repo)."""
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / gate), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


# ── fake swift coverage toolchain ────────────────────────────────────────────
# Shared by the coverage-gate swift tests and the coverage_swift selection
# tests. `swift`, `xcrun` and `xcodebuild` shims emit canned llvm-cov payloads
# modeled on REAL output captured from xcodebuild/llvm-cov on this machine
# (probe 2026-08-25), so the real bash gate → python helper chain is exercised
# without any Apple toolchain. Set swift_xcrun_log=... to record every xcrun
# invocation's argv (proves WHICH binary was paired with the profdata).

import json as _json
import stat as _stat

FAKE_EXPORT = {
    "data": [{"files": [
        {"filename": "PROJ/Sources/pkg/lib.swift",
         "summary": {"lines": {"count": 10, "covered": 5}}},
    ]}],
}

# llvm-cov show format: "LINE|COUNT|source". Lines 6-10 are the uncovered half.
FAKE_SHOW = "".join(
    f"{n:>5}|{'  0' if n > 5 else '  7'}|line {n}\n" for n in range(1, 11))


def mk_fake_swift_toolchain(root: Path, second_config: bool = False,
                            xcrun_log: Path | None = None) -> Path:
    bin_ = root / "fakebin"
    bin_.mkdir()
    pkg = root / "proj"
    src = pkg / "Sources" / "pkg"
    src.mkdir(parents=True)
    (src / "lib.swift").write_text(
        "\n".join(f"line {n}" for n in range(1, 11)) + "\n")

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
        xb = dd / "Build" / "Products" / cfg / "AppTests.xctest" / \
            "Contents" / "MacOS" / "AppTests"
        xb.parent.mkdir(parents=True)
        xb.write_text("#!/bin/sh\n")
        xb.chmod(xb.stat().st_mode | _stat.S_IEXEC)
    # Stale-binary pairing scenario: Debug was built LONG before the run that
    # wrote the profdata; Release (when present) is its mtime twin.
    import os as _os
    old = profdata.stat().st_mtime - 10_000
    _os.utime(dd / "Build/Products/Debug/AppTests.xctest/Contents/MacOS/AppTests",
              (old, old))
    if second_config:
        _os.utime(dd / "Build/Products/Release/AppTests.xctest/Contents/MacOS/AppTests",
                  (profdata.stat().st_mtime, profdata.stat().st_mtime))
    (dd / "Logs" / "Test").mkdir(parents=True)
    (dd / "Logs" / "Test" / "Test-App.xcresult").mkdir()

    export_file = root / "export.json"
    export_json = _json.dumps(FAKE_EXPORT).replace("PROJ", str(src.parent.parent))
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
        f'#!/bin/bash\n[ "$1 $2" = "--find swift" ] && {{ echo "{swift_path}"; exit 0; }}\nexit 1\n')
    xcrun.chmod(xcrun.stat().st_mode | _stat.S_IEXEC)
    return xcrun


# ── shared fake gh ───────────────────────────────────────────────────────────
# Stateful stub: `release view` succeeds iff that release was previously
# created. Shared by test_release_kit.py and test_release_hardening.py
# (was a 36-line verbatim duplicate in both — one copy now).
FAKE_GH = """\
#!/usr/bin/env bash
STATE="${GH_STATE:?}"; LOG="${GH_LOG:?}"
printf 'gh %s\\n' "$*" >> "$LOG"
cmd="$1"; shift
case "$cmd" in
  auth) exit 0 ;;
  api) exit 0 ;;
  release)
    sub="$1"; shift
    case "$sub" in
      view)
        tag=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --repo) shift 2 ;;
            --*) shift ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        [ -n "$tag" ] && [ -f "$STATE/rel-$tag" ] ;;
      create)
        tag=""; notes=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --notes-file) notes="$2"; shift 2 ;;
            --title|--repo) shift 2 ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        : > "$STATE/rel-$tag"
        [ -n "$notes" ] && cp "$notes" "$STATE/notes-$tag"
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
"""


def _build_goh() -> Path:
    r = subprocess.run(
        ["cargo", "build", "--message-format=json", "-p", "goh",
         "--manifest-path", str(REPO_ROOT / "Cargo.toml")],
        capture_output=True,
        text=True,
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


# Every `cargo build` the goh fixture runs this session, one line each. Read by
# test_goh_is_built_once_per_session: the count is the regression, not a timing.
GOH_BUILD_RECORD = "goh-builds.log"


def _shared_tmp(tmp_path_factory) -> Path:
    """The temp root every xdist worker shares (each worker's basetemp is a child of it).
    Without xdist, basetemp itself."""
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


@pytest.fixture(scope="session")
def goh(tmp_path_factory) -> Path:
    """The native goh binary, built ONCE per test session and read from a private copy.

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
    with open(shared / "goh-bin.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)          # released when the file closes
        if not published.exists():
            built = _build_goh()
            with open(shared / GOH_BUILD_RECORD, "a") as record:
                record.write(f"{os.environ.get('PYTEST_XDIST_WORKER', 'master')}\n")
            published.parent.mkdir(exist_ok=True)
            staging = published.with_suffix(".staging")
            shutil.copy2(built, staging)
            staging.rename(published)              # atomic: readers see all of it or none
    return published


@pytest.fixture(scope="session")
def goh_build_count(tmp_path_factory, goh) -> int:
    """How many times this session built goh (after this worker's own fixture resolved)."""
    return len((_shared_tmp(tmp_path_factory) / GOH_BUILD_RECORD).read_text().splitlines())
