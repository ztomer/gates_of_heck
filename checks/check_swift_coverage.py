#!/usr/bin/env python3
"""Fail when Swift test coverage is under a floor.

Companion to gates/swift_gate.sh, which runs tests and then calls this:

    check_swift_coverage.py --min 95                 # SPM
    check_swift_coverage.py --min 95 --xcode         # xcodebuild runs

Modes
-----
SPM   reads the codecov JSON written by `swift test --enable-code-coverage`
      and aggregates covered_lines / total_lines across every non-zero
      source file. swift_gate.sh passes the exact path it gets from
      `swift test --show-codecov-path` (`--spm-glob`); without one, both
      layouts SwiftPM has used are tried: `.build/<triple>/debug/codecov/`
      and, since Swift 6.3's build system, `.build/out/Products/<config>/
      codecov/` (found 2026-09-21: a green test step followed by "no
      payload" on antiknob).

xcode reads the newest `*.xcresult` under the pinned derived-data tree
      (.build/xcode-dd, which swift_gate.sh passes via -derivedDataPath)
      with `xcrun xccov view --report --json`, taking ONE record per target
      and excluding test code — the same rule the SPM path applies, from
      lib/swift_coverage_scope.py. It used to ask `xcrun xcresulttool get
      --format json` and walk the object tree instead; Xcode now refuses
      that without `--legacy`, so the mode is unrunnable without the
      fallback path below. Measured 2026-10-15.

Honesty rules (house rule #9): a MISSING payload is exit 2 with a named
reason — it must never masquerade as "0% coverage" or as success. Only an
actual measurement below the floor is exit 1.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import glob
import json
import os
import subprocess
import sys

DEFAULT_SPM_GLOBS = (
    ".build/*/debug/codecov/*.json",
    ".build/out/Products/*/codecov/*.json",
)
DEFAULT_DD = ".build/xcode-dd"


# ---- payload walking --------------------------------------------------------


# Sources llvm-cov reports that are not the code under test. The definition
# and its reasoning live in lib/swift_coverage_scope.py, shared with
# gates/coverage_swift.py -- which measured the same packages with NO
# exclusion at all until 2026-09-07 and so reported more than double the
# coverage on the same tree.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from swift_coverage_scope import EXCLUDED_MARKERS as _EXCLUDED_MARKERS, is_code_under_test


def _spm_file_lines(f: dict):
    """(covered, total) for one file entry, across llvm-cov export shapes.

    llvm.coverage.json.export moved the per-file counts under "summary" (seen
    in 3.0.1, Swift 6.3); older payloads carried total_lines/covered_lines at
    the file level. Read the modern shape first and fall back, so one checker
    serves both rather than reporting a healthy tree as corrupt JSON.
    """
    lines = (f.get("summary") or {}).get("lines")
    if isinstance(lines, dict):
        return int(lines.get("covered", 0) or 0), int(lines.get("count", 0) or 0)
    return int(f.get("covered_lines", 0) or 0), int(f.get("total_lines", 0) or 0)


def _spm_files(payload: dict):
    """Yield (filename, covered, total) from one SPM codecov JSON dict."""
    for dataset in payload.get("data", []):
        for f in dataset.get("files", []):
            name = f.get("filename") or f.get("name") or "?"
            if any(marker in name for marker in _EXCLUDED_MARKERS):
                continue
            covered, total = _spm_file_lines(f)
            if total > 0:
                yield name, covered, total


def _xcresult_records(node):
    """Depth-first walk collecting (target-ish name, covered, total).

    xcresulttool JSON shape varies across Xcode releases; rather than pin one
    schema we take any object whose coveredLines/lineCount pair is numeric.
    """
    found = []
    if isinstance(node, dict):
        covered, total = node.get("coveredLines"), node.get("lineCount")
        if (
            isinstance(covered, int)
            and isinstance(total, int)
            and not isinstance(covered, bool)
            and not isinstance(total, bool)
        ):
            label = node.get("name") or node.get("target") or node.get("identifier") or "?"
            found.append((str(label), covered, total))
        for v in node.values():
            found.extend(_xcresult_records(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(_xcresult_records(v))
    return found


def aggregate(records):
    """(percent, worst-first [(name, pct, covered, total)]) from raw triples."""
    tot_cov = sum(c for _, c, _ in records)
    tot_all = sum(t for _, _, t in records)
    per_file = [(n, (100.0 * c / t) if t else 100.0, c, t) for n, c, t in records if t > 0]
    per_file.sort(key=lambda r: r[1])
    pct = (100.0 * tot_cov / tot_all) if tot_all else 100.0
    return pct, per_file


# ---- payload loading --------------------------------------------------------


def load_spm(patterns):
    """[(name, cov, tot)] across every codecov JSON matching any pattern."""
    records: list[tuple[str, int, int]] = []
    paths = sorted({p for pat in patterns for p in glob.glob(pat)})
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                records.extend(_spm_files(json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"⚠ [swift_cov] unreadable codecov payload {p}: {exc}", file=sys.stderr)
    return records, paths


def newest_xcresult(dd_root: str):
    candidates = glob.glob(os.path.join(dd_root, "Logs", "Test", "*.xcresult"))
    return max(candidates, key=os.path.getmtime) if candidates else None


def _xccov_records(payload):
    """[(target, covered, total)] from `xccov view --report --json`.

    ONE RECORD PER TARGET, not per file. A floors key names a target, and
    `per_target` buckets a record by the first path segment — feed it files
    and every source file becomes its own one-file "target", which is a
    floors file nobody could write.

    Two exclusions, and they are the SAME rule the SPM path already applies
    (lib/swift_coverage_scope.py), not a second opinion about what coverage
    means:

      * a test bundle (`.xctest`) is dropped outright, and
      * files the shared scope rule calls not-code-under-test are skipped.

    The test exclusion is the one with teeth. A test file is ~100% covered by
    definition — it is the thing doing the running — so counting it means
    every test added raises coverage twice, once for the code it exercises
    and once for itself, and a floor set on that number can be met by
    writing tests that assert nothing. Measured on koffee_oss 2026-10-15:
    counting the xctest bundle put the package at 60.2% when the app itself
    is 40.4%. The SPM path had this fixed since 2026-09-07; the xcode path
    never applied the shared rule at all.
    """
    out = []
    for target in payload.get("targets") or []:
        name = target.get("name")
        if not isinstance(name, str) or not name or name.endswith(".xctest"):
            continue
        covered = total = 0
        for f in target.get("files") or []:
            if not isinstance(f, dict):
                continue
            path = f.get("path") or ""
            if path and not is_code_under_test(path):
                continue
            c, t = f.get("coveredLines"), f.get("executableLines")
            # bool is an int subclass in Python; a True must not count.
            if isinstance(c, int) and not isinstance(c, bool):
                covered += c
            if isinstance(t, int) and not isinstance(t, bool):
                total += t
        if total > 0:
            out.append((name, covered, total))
    return out


def _run_xccov(xc: str):
    """([(target, cov, tot)], reason). Exactly one of the two is meaningful."""
    try:
        out = subprocess.run(
            ["xcrun", "xccov", "view", "--report", "--json", xc],
            capture_output=True,
            text=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"xccov failed on {os.path.basename(xc)}: {exc}"
    if out.returncode != 0:
        return [], f"xccov exited {out.returncode}: {out.stderr.strip()[:200]}"
    try:
        payload = json.loads(out.stdout)
    except ValueError as exc:
        return [], f"xccov output was not JSON ({exc})"
    return _xccov_records(payload), None


def load_xcode(dd_root: str):
    """[(name, cov, tot)] from the newest xcresult; ([], reason) on trouble.

    xccov FIRST. The reader this replaced walked the xcresulttool object
    tree with `xcrun xcresulttool get --format json`, and Xcode now REFUSES
    that without `--legacy`: "This command is deprecated and will be removed
    in a future release" (measured 2026-10-15, Xcode 26). That made the
    whole step unrunnable -- exit 2, "cannot measure coverage" -- which is
    not a loud red that invites a fix but an infrastructure error every repo
    reads as "coverage is broken here, nevermind". No Swift repo in the
    estate had a coverage floor set, and now we know why: setting one went
    red on a healthy tree.

    The xcresulttool walk stays as a FALLBACK, with `--legacy`, for an
    xcresult xccov cannot read. When both readers fail the message names
    both, so the next reader is not left guessing which tool broke.
    """
    xc = newest_xcresult(dd_root)
    if xc is None:
        return [], f"no *.xcresult under {dd_root}/Logs/Test"

    records, why = _run_xccov(xc)
    if records:
        return records, None

    try:
        out = subprocess.run(
            [
                "xcrun",
                "xcresulttool",
                "get",
                "--legacy",
                "--path",
                xc,
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"{why}; xcresulttool failed on {xc}: {exc}"
    if out.returncode != 0:
        return [], (
            f"{why}; xcresulttool exited {out.returncode} on {xc}: {out.stderr.strip()[:200]}"
        )
    try:
        payload = json.loads(out.stdout)
    except ValueError as exc:
        return [], f"{why}; xcresulttool output was not JSON ({exc})"
    fallback = [r for r in _xcresult_records(payload) if r[2] > 0]
    if not fallback:
        return (
            [],
            (
                f"{why}; no coveredLines/lineCount records recognized in "
                f"{os.path.basename(xc)} (unsupported Xcode format?)"
            ),
        )
    return fallback, None


# ---- main -------------------------------------------------------------------


def sources_relative(path: str) -> str:
    """A source file's path relative to `Sources/`, or the path itself."""
    return path.split("/Sources/", 1)[1] if "/Sources/" in path else path


def spm_target_of(path: str) -> str:
    """The SPM target a source file belongs to.

    SPM lays targets out as `Sources/<Target>/...`, so the first segment
    after `Sources/` is the target name. Matches the convention
    gates/coverage_swift.py already uses, so one floors file describes a
    package to either measurement path.
    """
    rel = sources_relative(path)
    return rel.split("/", 1)[0] if "/" in rel else rel


def per_target(records):
    """{target: (covered, total)} over the measured records."""
    out: dict[str, list[int]] = {}
    for name, cov, tot in records:
        bucket = out.setdefault(spm_target_of(name), [0, 0])
        bucket[0] += cov
        bucket[1] += tot
    return {k: (v[0], v[1]) for k, v in out.items()}


def totals_under(records, prefix: str):
    """(covered, total) for every record under a `Sources/`-relative prefix.

    A floors key is a PATH PREFIX, not only a target name. A single segment
    ("AntiknobUI") behaves exactly as a target name did, so existing floors
    files are unaffected; a deeper key ("AntiknobUI/Core") puts a floor on
    part of a target.

    That matters because the alternative -- splitting a package into logic
    and view TARGETS purely so a floor can be aimed -- forces `public` onto
    every type crossing the new module boundary. Letting the coverage tool
    dictate a package's module structure is the wrong way round; a directory
    is enough to aim a floor at.

    Prefix matching is segment-wise, so "App/Core" never matches
    "App/CoreUI".
    """
    prefix = prefix.strip("/")
    covered = total = 0
    for name, cov, tot in records:
        rel = sources_relative(name)
        if rel == prefix or rel.startswith(prefix + "/"):
            covered += cov
            total += tot
    return covered, total


def load_floors(path: str):
    """Per-target floors, in the schema gates/coverage_gate.sh documents.

    Accepts the wrapper form {"targets": {...}, "tolerance": N} and the flat
    form {target: floor}. Anything else is a config error rather than an
    empty floor set: a floors file that parsed to nothing would enforce
    nothing while looking like it enforced something.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"✗ [swift_cov] cannot read floors file {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(raw, dict):
        print(f"✗ [swift_cov] floors file {path} must be a JSON object", file=sys.stderr)
        raise SystemExit(2)
    wrapper = any(k in raw for k in ("targets", "tolerance", "file_floor", "exempt", "slack"))
    targets = raw.get("targets", {}) if wrapper else raw
    floors = {k: float(v) for k, v in targets.items() if isinstance(v, (int, float))}
    if not floors:
        print(
            f"✗ [swift_cov] floors file {path} names no target floors — "
            "a floors file that enforces nothing is worse than none",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return floors, float(raw.get("tolerance", 0.0)) if wrapper else 0.0


def check_target_floors(records, floors, tolerance):
    """(exit_code, [lines]) for the per-target floors.

    A target named in the floors file that matches NO measured source is a
    hard error, never a pass. The sibling implementation in
    gates/coverage_swift.py scores an unmatched target 100% and lets it
    through, so a renamed or misspelled target there becomes a floor that
    cannot fail -- the same "inspected nothing, reported clean" shape this
    repo has a whole campaign about.
    """
    measured = per_target(records)
    lines, worst = [], 0
    for target in sorted(floors):
        floor = floors[target]
        cov, tot = totals_under(records, target)
        if tot == 0:
            lines.append(
                f"✗ [swift_cov] floors name '{target}', which "
                f"matched no measured source. Known targets: "
                f"{', '.join(sorted(measured)) or '(none)'}"
            )
            worst = max(worst, 2)
            continue
        pct = 100.0 * cov / tot
        if pct + 1e-9 < floor - tolerance:
            lines.append(
                f"✗ [swift_cov] target {target}: {pct:.2f}% "
                f"({cov}/{tot}) is under its {floor:g}% floor"
            )
            worst = max(worst, 1)
        else:
            lines.append(f"→ [swift_cov] target {target}: {pct:.2f}% >= {floor:g}%")
    return worst, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, required=True)
    ap.add_argument("--xcode", action="store_true")
    ap.add_argument(
        "--spm-glob",
        action="append",
        dest="spm_globs",
        help="codecov JSON path or glob (repeatable); default: both SwiftPM layouts",
    )
    ap.add_argument("--dd", default=DEFAULT_DD, help="derived-data root (xcode)")
    ap.add_argument(
        "--floors-json",
        default=os.environ.get("GOH_COV_FLOORS_JSON", ""),
        help="per-target floors, same schema as coverage_gate.sh",
    )
    args = ap.parse_args()

    if args.xcode:
        records, why = load_xcode(args.dd)
    else:
        spm_globs = args.spm_globs or list(DEFAULT_SPM_GLOBS)
        records, paths = load_spm(spm_globs)
        if paths and not records:
            # Payloads exist but none yielded measurable files (unreadable
            # JSON, or every entry has total_lines 0). Refuse with the count
            # rather than aggregating an empty record set into a fake 100%.
            why = (
                f"{len(paths)} payload(s) matched {' | '.join(spm_globs)} but "
                f"none contained measurable files — corrupt codecov JSON?"
            )
        else:
            why = (
                None
                if paths
                else (
                    f"no codecov payloads match {' | '.join(spm_globs)} — was 'swift "
                    f"test --enable-code-coverage' run?"
                )
            )

    if why:
        print(f"✗ [swift_cov] cannot measure coverage: {why}", file=sys.stderr)
        return 2

    pct, per_file = aggregate(records)

    # Per-target floors run BEFORE the package floor. The package number is
    # dominated by whichever target has the most lines -- on a SwiftUI app
    # that is the views -- so it can sit comfortably above its floor while
    # the logic target rots. Reporting the package as OK first would bury
    # that.
    if args.floors_json:
        floors, tolerance = load_floors(args.floors_json)
        code, lines = check_target_floors(records, floors, tolerance)
        for line in lines:
            print(line, file=sys.stderr if line.startswith("✗") else sys.stdout)
        if code:
            return code

    floor = args.min
    if pct + 1e-9 >= floor:
        print(f"→ [swift_cov] OK — {pct:.1f}% >= {floor:g}% ({len(per_file)} file(s))")
        return 0

    print(f"✗ [swift_cov] {pct:.1f}% is under the {floor:g}% floor:", file=sys.stderr)
    for n, p_, c, t in per_file[:20]:
        print(f"    {p_:6.1f}%  {c:>6}/{t:<6}  {n}", file=sys.stderr)
    if len(per_file) > 20:
        print(f"    … and {len(per_file) - 20} more", file=sys.stderr)
    print("\n  Write tests for what is uncovered; do not lower the floor.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
