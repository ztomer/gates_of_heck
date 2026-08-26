"""Shared fixtures: build throwaway git repos to run the checkers against.

Every disallowed glyph in this suite is built with chr(0x...) — never as a
literal — so the suite cannot trip gates_of_heck's own emoji gate.
"""

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
