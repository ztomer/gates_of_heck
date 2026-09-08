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

def floor_cmp(pct, floor):
    if pct + 1e-9 < floor:
        print(f"✗ [coverage] line coverage {pct:.2f}% is below the "
              f"{floor:g}% floor")
        return 1
    print(f"✓ [coverage] line coverage {pct:.2f}% (floor {floor:g}%)")
    return 0

def load_floors_config(path):
    """Load floors JSON. Supports wrapper {"targets":{}} or flat {tgt:floor}."""
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"✗ [coverage] cannot read floors file {path}: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(raw, dict):
        print(f"✗ [coverage] floors file {path} must be a JSON object", file=sys.stderr)
        sys.exit(2)
    if any(k in raw for k in ("targets", "file_floor", "tolerance", "exempt", "slack")):
        targets = raw.get("targets", {})
        return {"targets": {k: float(v) for k, v in targets.items()},
                "file_floor": float(raw["file_floor"]) if "file_floor" in raw else None,
                "tolerance": float(raw.get("tolerance", 0.5)),
                "slack": float(raw.get("slack", 2.0)),
                "exempt": dict(raw.get("exempt", {}))}
    return {"targets": {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))},
            "file_floor": None, "tolerance": 0.5, "slack": 2.0, "exempt": {}}

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "lib"))
sys.path.insert(0, _HERE)
from swift_coverage_scope import is_code_under_test  # noqa: E402
from coverage_markers import (  # noqa: E402
    TEST_DIR, adjust_coverage, check_marker_ceiling, find_exclusions, parse_markers,
)

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
        c for c in glob.glob(os.path.join(macos_dir, "*"))
        if os.path.isfile(c) and os.access(c, os.X_OK)
    )

def measured_files(files):
    """The llvm-cov file records that belong in the denominator.

    A named function rather than an inline comprehension so a test can call
    it. This filter was absent entirely, so Tests/ -- ~100% covered by
    definition, because it is the thing doing the running -- counted toward
    the floor, and so did SwiftPM's synthesised runner under .build. On
    antiknob that read 10.54% where the same tree measured 4.78% of its
    actual sources, and a floor set on the first number can be met by
    writing tests that assert nothing.

    The scope rule is shared with checks/check_swift_coverage.py so the two
    measurement paths cannot drift apart again.
    """
    return [f for f in files if is_code_under_test(f.get("filename", ""))]

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
        inner = bundle_executables(os.path.join(p, "Contents", "MacOS"))
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
        print("✗ [coverage] ambiguous test binaries — several are equally "
              f"near the profdata ({profdata}):", file=sys.stderr)
        for b in tied:
            print(f"    {b}", file=sys.stderr)
        sys.exit(2)
    return best

def run_xcodebuild(args, floor, ignore_re, include_re="", floors_json=None, marker_ceiling=None):
    xcresult = args.xcresult or os.environ.get("GOH_COV_XCRESULT") or ""
    if xcresult:
        precondition("xcrun", "xcrun xccov reads the xcresult")
        res = subprocess.run(
            ["xcrun", "xccov", "view", "--report", xcresult],
            capture_output=True, text=True)
        if res.returncode != 0:
            print(f"✗ [coverage] xccov failed on {xcresult}: "
                  f"{res.stderr.strip()[:200]}", file=sys.stderr)
            sys.exit(2)
        files = parse_xccov_report(res.stdout, ignore_re=ignore_re, include_re=include_re)
        if not files:
            print(f"✗ [coverage] no .swift file rows recognized in "
                  f"{xcresult} — wrong bundle or empty report?",
                  file=sys.stderr)
            sys.exit(2)
        covered = sum(c for c, _ in files.values())
        total = sum(t for _, t in files.values())
        pct = (100.0 * covered / total) if total else 0.0
        _, errors = find_exclusions(os.getcwd(), ignore_re, include_re)
        if errors:
            print("✗ [coverage] cov:ignore markers exist but the xcresult "
                  "mode has no line-level data to honor them with:", file=sys.stderr)
            for e in errors[:10]:
                print(f"    {e}", file=sys.stderr)
            sys.exit(2)
        if floors_json:
            cfg = load_floors_config(floors_json)
            import collections
            per = collections.defaultdict(lambda: [0, 0])
            for path, (c, t) in files.items():
                rel = path.split("/Sources/", 1)[1] if "/Sources/" in path else path
                tgt = rel.split("/", 1)[0] if "/" in rel else rel
                per[tgt][0] += t
                per[tgt][1] += c
            for tgt, fval in cfg["targets"].items():
                tot, cov = per.get(tgt, [0, 0])
                cur = 100.0 * cov / tot if tot else 100.0
                if cur + 1e-9 < fval - cfg.get("tolerance", 0.5):
                    print(f"✗ [coverage] target {tgt}: {cur:.2f}% below {fval:g}%", file=sys.stderr)
                    sys.exit(1)
        if marker_ceiling:
            check_marker_ceiling(0, marker_ceiling)
        if floor is None and floors_json:
            sys.exit(0)
        sys.exit(floor_cmp(pct, floor))

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
        for bundle in glob.glob(os.path.join(
                dd, "Build", "Products", cfg, "*.xctest")):
            binaries += bundle_executables(
                os.path.join(bundle, "Contents", "MacOS"))
    if not binaries:
        print(f"✗ [coverage] no .xctest binary under {dd}/Build/Products",
              file=sys.stderr)
        sys.exit(2)
    return pick_binary(profs[-1], binaries), profs[-1]

def process(binary, profdata, proj, floor, ignore_re, include_re="", floors_json=None, marker_ceiling=None):
    """Shared llvm-cov report processing: totals + cov:ignore forgiveness."""
    if not include_re:
        include_re = os.environ.get("GOH_COV_INCLUDE_RE", "")
    if not floors_json:
        floors_json = os.environ.get("GOH_COV_FLOORS_JSON", "") or None
    if not marker_ceiling:
        marker_ceiling = os.environ.get("GOH_COV_MARKER_CEILING", "") or None
        if not marker_ceiling:
            for cand in (os.path.join(proj, ".coverage-forgiveness-ceiling.json"),
                         ".coverage-forgiveness-ceiling.json"):
                if os.path.exists(cand):
                    marker_ceiling = cand
                    break
    cmd = ["xcrun", "llvm-cov", "export", "-summary-only", binary,
           f"-instr-profile={profdata}"]
    if ignore_re:
        cmd.append(f"-ignore-filename-regex={ignore_re}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        # Say what actually happened. This used to claim the JSON could not
        # be parsed, when llvm-cov had not produced any -- so a wrong binary
        # or a stale profdata was reported as a parser problem, and the one
        # line that named the real cause was thrown away.
        print(f"✗ [coverage] llvm-cov failed (exit {res.returncode}) reading "
              f"{binary}", file=sys.stderr)
        for line in (res.stderr or res.stdout).strip().splitlines()[:5]:
            print(f"    {line}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(res.stdout)["data"][0]
    files = measured_files(data.get("files", []))
    if include_re:
        try:
            pat = re.compile(include_re)
        except re.error as exc:
            print(f"✗ [coverage] bad --include regex: {exc}", file=sys.stderr)
            sys.exit(2)
        files = [f for f in files if pat.search(f.get("filename", ""))]
    raw_total = sum(f["summary"]["lines"]["count"] for f in files)
    raw_covered = sum(f["summary"]["lines"]["covered"] for f in files)
    if raw_total == 0:
        print("✗ [coverage] no coverable lines found", file=sys.stderr)
        sys.exit(1)

    exclusions, errors = find_exclusions(proj, ignore_re, include_re)
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
    if marker_ceiling:
        check_marker_ceiling(forgiven, marker_ceiling)
    if floors_json:
        cfg = load_floors_config(floors_json)
        import collections
        per = collections.defaultdict(lambda: [0, 0])
        per_files = collections.defaultdict(list)
        for f in files:
            fname = f.get("filename", "")
            rel = fname.split("/Sources/", 1)[1] if "/Sources/" in fname else os.path.relpath(fname, proj) if fname.startswith(proj) else fname
            tgt = rel.split("/", 1)[0] if "/" in rel else rel
            per[tgt][0] += f["summary"]["lines"]["count"]
            per[tgt][1] += f["summary"]["lines"]["covered"]
            per_files[tgt].append((rel, f["summary"]["lines"]["count"], f["summary"]["lines"]["covered"]))
        failures = []
        for tgt, fval in cfg["targets"].items():
            tot, cov = per.get(tgt, [0, 0])
            cur = 100.0 * cov / tot if tot else 100.0
            tol = cfg.get("tolerance", 0.5)
            if cur + 1e-9 < fval - tol:
                failures.append(f"{tgt} fell to {cur:.1f}%, below its floor {fval:.1f}%")
            elif cur > fval + cfg.get("slack", 2.0):
                print(f"  ⚠ {tgt} is {cur:.1f}% against {fval:.1f}% — re-record")
        if cfg.get("exempt"):
            present = {r for flist in per_files.values() for r, _, _ in flist}
            stale = sorted(set(cfg["exempt"]) - present)
            if stale:
                print(f"✗ {len(stale)} exemption(s) stale (file gone).", file=sys.stderr)
                sys.exit(1)
        if cfg.get("file_floor") is not None:
            ff = cfg["file_floor"]
            below = []
            for tgt, flist in per_files.items():
                for rel, tot, cov in flist:
                    if rel in cfg["exempt"]:
                        continue
                    cur = 100.0 * cov / tot if tot else 100.0
                    if cur + 1e-9 < ff:
                        below.append((rel, tot, cov, cur))
            if below:
                print(f"✗ {len(below)} file(s) below per-file floor {ff:g}%", file=sys.stderr)
                failures.append(f"{len(below)} file(s) below per-file floor")
        if failures:
            for f in failures:
                print(f"✗ {f}", file=sys.stderr)
            sys.exit(1)
        if floor is not None:
            return floor_cmp(pct, floor)
        print(f"✓ [coverage] all per-target floors passed (gated {pct:.2f}%)")
        return 0
    return floor_cmp(pct, floor)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", type=float, required=False, default=None)
    ap.add_argument("--proj", default=os.getcwd())
    ap.add_argument("--ignore", default="")
    ap.add_argument("--include", default="")
    ap.add_argument("--floors-json", dest="floors_json", default="")
    ap.add_argument("--marker-ceiling", dest="marker_ceiling", default="")
    ap.add_argument("--engine", choices=["spm", "xcodebuild"], default="spm")
    ap.add_argument("--xcresult", default="")
    ap.add_argument("--scheme", default="")
    ap.add_argument("--dd", default="")
    ap.add_argument("--destination", default="platform=macOS")
    args = ap.parse_args()
    if not args.include:
        args.include = os.environ.get("GOH_COV_INCLUDE_RE", "")
    if not args.floors_json:
        args.floors_json = os.environ.get("GOH_COV_FLOORS_JSON", "")
    if not args.marker_ceiling:
        args.marker_ceiling = os.environ.get("GOH_COV_MARKER_CEILING", "")
    if args.floor is None and not args.floors_json:
        print("✗ [coverage] no coverage floor: pass --floor N or --floors-json PATH ", file=sys.stderr)
        sys.exit(2)
    if args.engine == "spm":
        binary, profdata = run_spm(args.ignore)
    else:
        binary, profdata = run_xcodebuild(args, args.floor, args.ignore,
                                          args.include, args.floors_json or None,
                                          args.marker_ceiling or None)
    return process(binary, profdata, args.proj, args.floor, args.ignore,
                   args.include, args.floors_json or None, args.marker_ceiling or None)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  # the discipline IS the point:
        # a crash must exit 2 naming the error — an uncaught traceback exits 1,
        # which consumers read as "below floor", a coverage number that never was.
        print(f"✗ [coverage] coverage_swift.py failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
