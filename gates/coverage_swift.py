#!/usr/bin/env python3
"""Swift half of coverage_gate.sh — both engines, one report processor.

Engines (--engine, or GOH_COV_SWIFT_ENGINE; default spm):

  spm         swift test --enable-code-coverage → llvm-cov export/show on the
              .xctest binary + default.profdata.
  xcodebuild  xcodebuild test -enableCodeCoverage YES -scheme <scheme>
              -derivedDataPath <dd> → llvm-cov export/show on the Coverage
              profdata + test binary that xcodebuild emits under dd/Build.

  GOH_COV_XCRESULT / --xcresult PATH (xcodebuild engine): skip the test run
  and derive totals from an EXISTING xcresult via `xcrun xccov view --report`.
  xccov has NO line-level data (its --file view is a silent no-op on current
  Xcode, probed 2026-08-25), so cov:ignore markers cannot be honored there:
  if markers exist in sources this mode is a named exit-2 refusal, never
  silent forgiveness.

Region forgiveness (ported from ZeroThunder tools/gated_coverage.py; the
reason requirement added per the house rule that forgiveness must say WHY):

    // cov:ignore: <reason>                  (this line)
    // cov:ignore-start: <reason> ... // cov:ignore-end   (inclusive block)

A marked line that llvm-cov shows UNCOVERED is removed from the denominator
only; a marker on a covered line is a no-op. A marker WITHOUT a reason, or a
start without an end, is a gate error (exit 2) — not forgiveness.

Exit codes: 0 pass | 1 below floor | 2 usage/config/precondition error.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

SINGLE = re.compile(r"//\s*cov:ignore\b(?!-)")
START = re.compile(r"//\s*cov:ignore-start\b")
END = re.compile(r"//\s*cov:ignore-end\b")

TEST_DIR = re.compile(r"(^|/)(Tests|.*Tests)/")


def parse_markers(lines):
    """(excluded {1-indexed line}, errors [str]) from one source file."""
    excluded = set()
    errors = []
    open_at = None
    in_block = False
    for i, line in enumerate(lines, start=1):
        if START.search(line):
            reason = re.search(r"cov:ignore-start\s*:\s*(.+?)\s*$", line)
            if not reason or not reason.group(1).strip():
                errors.append(f"{i}: cov:ignore-start without a reason")
            open_at = i
            in_block = True
            excluded.add(i)
        elif END.search(line):
            excluded.add(i)
            in_block = False
            open_at = None
        elif SINGLE.search(line):
            reason = re.search(r"cov:ignore\s*:\s*(.+?)\s*$", line)
            if not reason or not reason.group(1).strip():
                errors.append(f"{i}: cov:ignore without a reason")
            else:
                excluded.add(i)
        elif in_block:
            excluded.add(i)
    if open_at is not None:
        errors.append(f"{open_at}: unclosed cov:ignore-start block")
    return excluded, errors


def find_exclusions(proj, ignore_re):
    """({abspath: excluded lines}, [errors]) over non-test Swift sources."""
    out = {}
    errors = []
    for path in sorted(glob.glob(os.path.join(proj, "**", "*.swift"),
                                 recursive=True)):
        rel = os.path.relpath(path, proj)
        if TEST_DIR.search("/" + rel):
            continue
        if ignore_re and re.search(ignore_re, path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        excluded, errs = parse_markers(lines)
        for e in errs:
            errors.append(f"{rel}:{e}")
        if excluded:
            out[path] = excluded
    return out, errors


def adjust_coverage(raw_total, raw_covered, counts, excluded):
    """(pct, forgiven): drop only UNCOVERED marker lines from the total."""
    forgiven = sum(1 for ln in excluded if counts.get(ln) == 0)
    adj_total = raw_total - forgiven
    pct = (100.0 * raw_covered / adj_total) if adj_total else 0.0
    return pct, forgiven


def llvm_cov_line_counts(binary, profdata, path):
    """line -> covered(1)/uncovered(0) via llvm-cov show (gated_coverage port)."""
    res = subprocess.run(
        ["xcrun", "llvm-cov", "show", binary,
         f"-instr-profile={profdata}", path],
        capture_output=True, text=True, check=False)
    counts = {}
    for raw in res.stdout.splitlines():
        parts = raw.split("|", 2)
        if len(parts) < 2:
            continue
        ln, cnt = parts[0].strip(), parts[1].strip()
        if not ln.isdigit() or cnt == "":
            continue
        counts[int(ln)] = 0 if cnt == "0" else 1
    return counts


# koffee_big check_coverage.py LINE_RE, verified against live xccov output.
XCCOV_ROW = re.compile(
    r"^\s*(.+\.swift)\s+([0-9.]+)%\s+\(([0-9]+)/([0-9]+)\)\s*$")


def parse_xccov_report(report, ignore_re=None):
    """{path: (covered, total)} for top-level file rows, ignore regex applied."""
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
        if ignore_re and re.search(ignore_re, path):
            continue
        files[path] = (int(covered), int(total))
    return files


def floor_cmp(pct, floor):
    if pct + 1e-9 < floor:
        print(f"✗ [coverage] line coverage {pct:.2f}% is below the "
              f"{floor:g}% floor")
        return 1
    print(f"✓ [coverage] line coverage {pct:.2f}% (floor {floor:g}%)")
    return 0


def precondition(name, why):
    if shutil.which(name) is None:
        print(f"✗ [coverage] '{name}' not found on PATH — required for the "
              f"swift mode ({why})", file=sys.stderr)
        sys.exit(2)


def run_spm(ignore_re):
    precondition("swift", "swift test --enable-code-coverage")
    precondition("xcrun", "xcrun llvm-cov reads the profdata")
    if subprocess.run(["swift", "test", "--enable-code-coverage"],
                      capture_output=True).returncode != 0:
        print("✗ [coverage] tests failed while collecting coverage",
              file=sys.stderr)
        sys.exit(1)
    bin_path = subprocess.run(["swift", "build", "--show-bin-path"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    xctest = None
    for p in glob.glob(os.path.join(bin_path, "**", "*.xctest"),
                       recursive=True):
        macos = os.path.join(p, "Contents", "MacOS")
        inner = glob.glob(os.path.join(macos, "*"))
        if inner:
            xctest = inner[0]
            break
    if not xctest:
        print(f"✗ [coverage] no .xctest binary under {bin_path}",
              file=sys.stderr)
        sys.exit(2)
    profdata = os.path.join(bin_path, "codecov", "default.profdata")
    if not os.path.exists(profdata):
        print(f"✗ [coverage] no profdata at {profdata} — swift test did not "
              "emit coverage", file=sys.stderr)
        sys.exit(2)
    return xctest, profdata


def run_xcodebuild(args, floor, ignore_re):
    xcresult = args.xcresult or os.environ.get("GOH_COV_XCRESULT") or ""
    if xcresult:
        # Existing-bundle mode: totals only, via xccov text (proven format).
        precondition("xcrun", "xcrun xccov reads the xcresult")
        res = subprocess.run(
            ["xcrun", "xccov", "view", "--report", xcresult],
            capture_output=True, text=True)
        if res.returncode != 0:
            print(f"✗ [coverage] xccov failed on {xcresult}: "
                  f"{res.stderr.strip()[:200]}", file=sys.stderr)
            sys.exit(2)
        files = parse_xccov_report(res.stdout, ignore_re=ignore_re)
        if not files:
            print(f"✗ [coverage] no .swift file rows recognized in "
                  f"{xcresult} — wrong bundle or empty report?",
                  file=sys.stderr)
            sys.exit(2)
        covered = sum(c for c, _ in files.values())
        total = sum(t for _, t in files.values())
        pct = (100.0 * covered / total) if total else 0.0
        _, errors = find_exclusions(os.getcwd(), ignore_re)
        if errors:
            print("✗ [coverage] cov:ignore markers exist but the xcresult "
                  "mode has no line-level data to honor them with:", file=sys.stderr)
            for e in errors[:10]:
                print(f"    {e}", file=sys.stderr)
            sys.exit(2)
        return floor_cmp(pct, floor)

    # Run mode: full llvm-cov pipeline on what xcodebuild emits.
    precondition("xcodebuild", "xcodebuild test -enableCodeCoverage YES")
    precondition("xcrun", "xcrun llvm-cov reads the profdata")
    scheme = args.scheme or os.environ.get("GOH_COV_SCHEME") or ""
    if not scheme:
        print("✗ [coverage] engine xcodebuild needs a scheme: pass --scheme "
              "or set GOH_COV_SCHEME", file=sys.stderr)
        sys.exit(2)
    dd = args.dd or os.environ.get("GOH_COV_DD") or ".build/xcode-dd"
    run = subprocess.run(
        ["xcodebuild", "test", "-scheme", scheme,
         "-destination", args.destination, "-derivedDataPath", dd,
         "-enableCodeCoverage", "YES", "-quiet"],
        capture_output=True, text=True)
    if run.returncode != 0:
        tail = (run.stderr or run.stdout).strip().splitlines()[-3:]
        print("✗ [coverage] xcodebuild test failed:", file=sys.stderr)
        for ln in tail:
            print(f"    {ln}", file=sys.stderr)
        sys.exit(1)
    profs = sorted(glob.glob(os.path.join(
        dd, "Build", "ProfileData", "*", "Coverage*.profdata")), key=os.path.getmtime)
    if not profs:
        print(f"✗ [coverage] no Coverage*.profdata under {dd}/Build/"
              "ProfileData — did the test run emit coverage?", file=sys.stderr)
        sys.exit(2)
    binaries = []
    for cfg in ("Debug", "Release"):
        binaries += glob.glob(os.path.join(
            dd, "Build", "Products", cfg, "*.xctest",
            "Contents", "MacOS", "*"))
    if not binaries:
        print(f"✗ [coverage] no .xctest binary under {dd}/Build/Products",
              file=sys.stderr)
        sys.exit(2)
    return profs[-1], binaries[0]


def process(binary, profdata, proj, floor, ignore_re):
    """Shared llvm-cov report processing: totals + cov:ignore forgiveness."""
    cmd = ["xcrun", "llvm-cov", "export", "-summary-only", binary,
           f"-instr-profile={profdata}"]
    if ignore_re:
        cmd.append(f"-ignore-filename-regex={ignore_re}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("✗ [coverage] could not parse llvm-cov JSON summary",
              file=sys.stderr)
        sys.exit(1)
    data = json.loads(res.stdout)["data"][0]
    raw_total = sum(f["summary"]["lines"]["count"] for f in data["files"])
    raw_covered = sum(f["summary"]["lines"]["covered"] for f in data["files"])
    if raw_total == 0:
        print("✗ [coverage] no coverable lines found", file=sys.stderr)
        sys.exit(1)

    exclusions, errors = find_exclusions(proj, ignore_re)
    if errors:
        print(f"✗ [coverage] {len(errors)} invalid cov:ignore marker(s) — "
              "forgiveness requires a stated reason:", file=sys.stderr)
        for e in errors[:10]:
            print(f"    {e}", file=sys.stderr)
        sys.exit(2)

    forgiven = 0
    rows = []
    for path, lines in exclusions.items():
        counts = llvm_cov_line_counts(binary, profdata, path)
        n = sum(1 for ln in lines if counts.get(ln) == 0)
        if n:
            forgiven += n
            rows.append((os.path.relpath(path, proj), n))

    pct = (100.0 * raw_covered / (raw_total - forgiven)) if raw_total else 0.0
    if rows:
        print(f"→ [coverage] forgave {forgiven} uncovered marked line(s), "
              f"across {len(rows)} file(s)")
        for name, n in sorted(rows, key=lambda r: -r[1]):
            print(f"    {n:>4} lines  {name}")
    return floor_cmp(pct, floor)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=float, required=True)
    ap.add_argument("--proj", default=os.getcwd())
    ap.add_argument("--ignore", default="")
    ap.add_argument("--engine", choices=["spm", "xcodebuild"], default="spm")
    ap.add_argument("--xcresult", default="")
    ap.add_argument("--scheme", default="")
    ap.add_argument("--dd", default="")
    ap.add_argument("--destination", default="platform=macOS")
    args = ap.parse_args()

    if args.engine == "spm":
        binary, profdata = run_spm(args.ignore)
    else:
        rc = run_xcodebuild(args, args.floor, args.ignore)
        return rc
    return process(binary, profdata, args.proj, args.floor, args.ignore)


if __name__ == "__main__":
    sys.exit(main())
