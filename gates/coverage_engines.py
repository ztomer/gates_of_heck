#!/usr/bin/env python3
"""The two coverage ENGINES: produce a report. `coverage_swift.py` judges it.

Split out of `gates/coverage_swift.py`, which grew past the house 500-line cap
after `ruff format` at the new line length. The seam is the one the original
docstring already drew -- "both engines, one report processor" -- and it is the
only seam here that is not arbitrary: everything in this module answers "where
does the coverage data come from?", and everything in the other answers "is it
enough?". Nothing decides a floor here, and nothing launches a test run there.

What moved here, and why each is on this side:

  * running the tests with coverage (`run_spm`, `run_xcodebuild`) and pairing
    the binary to the profdata (`pick_binary`, `bundle_executables`) -- pure
    discovery, and the half that needs a toolchain to be on PATH at all;
  * reading the rows out of an xccov report (`xccov_report`, `parse_xccov_report`)
    -- still acquisition: the report has not been interpreted when this returns.

`precondition` moved because it exists only for the launches below: a missing
`swift` or `xcodebuild` is an engine problem, and the report processor never
starts a process whose absence it should care about.

The dependencies run ONE way, deliberately. `coverage_swift.py` imports this
module; nothing here imports it back, which is why `floor_cmp`,
`load_floors_config` and `unmatched_target` stayed where they are instead of
coming along -- they are policy, and this half makes no policy.

Exit codes are unchanged by the move: 0 pass | 1 below floor | 2 usage/config/
precondition error, and every refusal below still names what it could not do.
"""

import glob
import os
import re
import shutil
import subprocess
import sys

# koffee_big check_coverage.py LINE_RE, verified against live xccov output.
XCCOV_ROW = re.compile(r"^\s*(.+\.swift)\s+([0-9.]+)%\s+\(([0-9]+)/([0-9]+)\)\s*$")


def parse_xccov_report(report, ignore_re=None, include_re=None):
    """{path: (covered, total)} for top-level file rows, include+ignore applied."""
    files = {}
    indent = None
    for line in report.splitlines():
        m = XCCOV_ROW.match(line)
        if not m:
            continue
        path, _pct, covered, total = m.groups()
        lead = len(line) - len(line.lstrip())
        # File rows are indented once under a target row; function rows nest
        # deeper. Track the shallowest indent seen as the file level.
        if indent is None or lead < indent:
            indent = lead
        if lead != indent:
            continue
        if include_re and not re.search(include_re, path):
            continue
        if ignore_re and re.search(ignore_re, path):
            continue
        files[path] = (int(covered), int(total))
    return files


def precondition(name, why):
    if shutil.which(name) is None:
        print(
            f"✗ [coverage] '{name}' not found on PATH — required for the swift mode ({why})",
            file=sys.stderr,
        )
        sys.exit(2)


def bundle_executables(macos_dir):
    """The runnable binaries inside an .xctest bundle's Contents/MacOS.

    A bundle built with debug symbols -- which is how `swift test` builds by
    default -- holds a `.dSYM` DIRECTORY beside the executable. Both matched
    the plain `*` glob this used to take the first entry of, so on any such
    package llvm-cov was handed the .dSYM and failed with "Is a directory".
    Coverage could not be measured at all, and the caller reported it as a
    JSON parse error, which named the wrong thing entirely.

    The existing selection tests did not catch it because their fixtures put
    the executable in Contents/MacOS and nothing else: a harness that builds
    its own inputs only ever tests the shapes it thought to build.

    A directory is never the executable, and neither is a regular file
    without the execute bit. Sorted so selection is deterministic rather
    than dependent on the order the filesystem hands back.
    """
    return sorted(
        c
        for c in glob.glob(os.path.join(macos_dir, "*"))
        if os.path.isfile(c) and os.access(c, os.X_OK)
    )


def run_spm(ignore_re):
    precondition("swift", "swift test --enable-code-coverage")
    precondition("xcrun", "xcrun llvm-cov reads the profdata")
    if (
        subprocess.run(["swift", "test", "--enable-code-coverage"], capture_output=True).returncode
        != 0
    ):
        print("✗ [coverage] tests failed while collecting coverage", file=sys.stderr)
        sys.exit(1)
    bin_path = subprocess.run(
        ["swift", "build", "--show-bin-path"], capture_output=True, text=True, check=True
    ).stdout.strip()
    xctest = None
    for p in glob.glob(os.path.join(bin_path, "**", "*.xctest"), recursive=True):
        inner = bundle_executables(os.path.join(p, "Contents", "MacOS"))
        if inner:
            xctest = inner[0]
            break
    if not xctest:
        print(f"✗ [coverage] no .xctest binary under {bin_path}", file=sys.stderr)
        sys.exit(2)
    profdata = os.path.join(bin_path, "codecov", "default.profdata")
    if not os.path.exists(profdata):
        print(
            f"✗ [coverage] no profdata at {profdata} — swift test did not emit coverage",
            file=sys.stderr,
        )
        sys.exit(2)
    return xctest, profdata


def pick_binary(profdata, binaries):
    """The .xctest binary whose mtime is NEAREST the chosen profdata's.

    The old selection took the profdata by newest mtime but the binary by a
    Debug-first glob: whenever more than one configuration's binary existed,
    a fresh profdata was paired with a STALE binary and llvm-cov reported
    against mismatched code. Mtime-nearest is the only pairing signal
    xcodebuild leaves; genuine ambiguity is a named exit 2, never a guess
    (cpp-mode precedent).
    """
    pt = os.path.getmtime(profdata)
    ranked = sorted((abs(os.path.getmtime(b) - pt), b) for b in binaries)
    best_delta, best = ranked[0]
    tied = [b for delta, b in ranked if delta == best_delta]
    if len(tied) > 1:
        print(
            "✗ [coverage] ambiguous test binaries — several are equally "
            f"near the profdata ({profdata}):",
            file=sys.stderr,
        )
        for b in tied:
            print(f"    {b}", file=sys.stderr)
        sys.exit(2)
    return best


def xccov_report(xcresult):
    """The `xcrun xccov view --report` text for an existing xcresult."""
    precondition("xcrun", "xcrun xccov reads the xcresult")
    res = subprocess.run(
        ["xcrun", "xccov", "view", "--report", xcresult], capture_output=True, text=True
    )
    if res.returncode != 0:
        print(
            f"✗ [coverage] xccov failed on {xcresult}: {res.stderr.strip()[:200]}",
            file=sys.stderr,
        )
        sys.exit(2)
    return res.stdout


def run_xcodebuild(args):
    """Run mode: the full llvm-cov pipeline on what xcodebuild emits."""
    precondition("xcodebuild", "xcodebuild test -enableCodeCoverage YES")
    precondition("xcrun", "xcrun llvm-cov reads the profdata")
    scheme = args.scheme or os.environ.get("GOH_COV_SCHEME") or ""
    if not scheme:
        print(
            "✗ [coverage] engine xcodebuild needs a scheme: pass --scheme or set GOH_COV_SCHEME",
            file=sys.stderr,
        )
        sys.exit(2)
    dd = args.dd or os.environ.get("GOH_COV_DD") or ".build/xcode-dd"
    run = subprocess.run(
        [
            "xcodebuild",
            "test",
            "-scheme",
            scheme,
            "-destination",
            args.destination,
            "-derivedDataPath",
            dd,
            "-enableCodeCoverage",
            "YES",
            "-quiet",
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        tail = (run.stderr or run.stdout).strip().splitlines()[-3:]
        print("✗ [coverage] xcodebuild test failed:", file=sys.stderr)
        for ln in tail:
            print(f"    {ln}", file=sys.stderr)
        sys.exit(1)
    profs = sorted(
        glob.glob(os.path.join(dd, "Build", "ProfileData", "*", "Coverage*.profdata")),
        key=os.path.getmtime,
    )
    if not profs:
        print(
            f"✗ [coverage] no Coverage*.profdata under {dd}/Build/"
            "ProfileData — did the test run emit coverage?",
            file=sys.stderr,
        )
        sys.exit(2)
    binaries = []
    for cfg in ("Debug", "Release"):
        for bundle in glob.glob(os.path.join(dd, "Build", "Products", cfg, "*.xctest")):
            binaries += bundle_executables(os.path.join(bundle, "Contents", "MacOS"))
    if not binaries:
        print(f"✗ [coverage] no .xctest binary under {dd}/Build/Products", file=sys.stderr)
        sys.exit(2)
    return pick_binary(profs[-1], binaries), profs[-1]
