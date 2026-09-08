#!/usr/bin/env python3
"""Fail when Swift test coverage is under a floor.

Companion to gates/swift_gate.sh, which runs tests and then calls this:

    check_swift_coverage.py --min 95                 # SPM
    check_swift_coverage.py --min 95 --xcode         # xcodebuild runs

Modes
-----
SPM   globs `.build/<arch>/debug/codecov/*.json` (written by
      `swift test --enable-code-coverage`) and aggregates
      covered_lines / total_lines across every non-zero source file.

xcode reads the newest `*.xcresult` under the pinned derived-data tree
      (.build/xcode-dd, which swift_gate.sh passes via -derivedDataPath),
      asks `xcrun xcresulttool get --format json`, and walks the tree for
      every object carrying numeric coveredLines/lineCount pairs — the
      per-target coverage records.

Honesty rules (house rule #9): a MISSING payload is exit 2 with a named
reason — it must never masquerade as "0% coverage" or as success. Only an
actual measurement below the floor is exit 1.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

DEFAULT_SPM_GLOB = ".build/*/debug/codecov/*.json"
DEFAULT_DD = ".build/xcode-dd"


# ---- payload walking --------------------------------------------------------


# Sources llvm-cov reports that are not the code under test. The definition
# and its reasoning live in lib/swift_coverage_scope.py, shared with
# gates/coverage_swift.py -- which measured the same packages with NO
# exclusion at all until 2026-09-07 and so reported more than double the
# coverage on the same tree.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
from swift_coverage_scope import EXCLUDED_MARKERS as _EXCLUDED_MARKERS  # noqa: E402


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
            label = (
                node.get("name")
                or node.get("target")
                or node.get("identifier")
                or "?"
            )
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
    per_file = [
        (n, (100.0 * c / t) if t else 100.0, c, t)
        for n, c, t in records
        if t > 0
    ]
    per_file.sort(key=lambda r: r[1])
    pct = (100.0 * tot_cov / tot_all) if tot_all else 100.0
    return pct, per_file


# ---- payload loading --------------------------------------------------------


def load_spm(pattern: str):
    """[(name, cov, tot)] across every matching codecov JSON; [] if none."""
    records: list[tuple[str, int, int]] = []
    paths = sorted(glob.glob(pattern))
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                records.extend(_spm_files(json.load(fh)))
        except (OSError, ValueError) as exc:
            print(f"⚠ [swift_cov] unreadable codecov payload {p}: {exc}",
                  file=sys.stderr)
    return records, paths


def newest_xcresult(dd_root: str):
    candidates = glob.glob(os.path.join(dd_root, "Logs", "Test", "*.xcresult"))
    return max(candidates, key=os.path.getmtime) if candidates else None


def load_xcode(dd_root: str):
    """[(name, cov, tot)] from the newest xcresult; ([], reason) on trouble."""
    xc = newest_xcresult(dd_root)
    if xc is None:
        return [], f"no *.xcresult under {dd_root}/Logs/Test"
    try:
        out = subprocess.run(
            ["xcrun", "xcresulttool", "get", "--path", xc, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"xcresulttool failed on {xc}: {exc}"
    if out.returncode != 0:
        return [], f"xcresulttool exited {out.returncode} on {xc}: {out.stderr.strip()[:200]}"
    try:
        payload = json.loads(out.stdout)
    except ValueError as exc:
        return [], f"xcresulttool output was not JSON ({exc})"
    records = [r for r in _xcresult_records(payload) if r[2] > 0]
    if not records:
        return [], f"no coveredLines/lineCount records recognized in {xc} (unsupported Xcode format?)"
    return records, None


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
    except Exception as exc:
        print(f"✗ [swift_cov] cannot read floors file {path}: {exc}",
              file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(raw, dict):
        print(f"✗ [swift_cov] floors file {path} must be a JSON object",
              file=sys.stderr)
        raise SystemExit(2)
    wrapper = any(k in raw for k in ("targets", "tolerance", "file_floor",
                                     "exempt", "slack"))
    targets = raw.get("targets", {}) if wrapper else raw
    floors = {k: float(v) for k, v in targets.items()
              if isinstance(v, (int, float))}
    if not floors:
        print(f"✗ [swift_cov] floors file {path} names no target floors — "
              "a floors file that enforces nothing is worse than none",
              file=sys.stderr)
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
            lines.append(f"✗ [swift_cov] floors name '{target}', which "
                         f"matched no measured source. Known targets: "
                         f"{', '.join(sorted(measured)) or '(none)'}")
            worst = max(worst, 2)
            continue
        pct = 100.0 * cov / tot
        if pct + 1e-9 < floor - tolerance:
            lines.append(f"✗ [swift_cov] target {target}: {pct:.2f}% "
                         f"({cov}/{tot}) is under its {floor:g}% floor")
            worst = max(worst, 1)
        else:
            lines.append(f"→ [swift_cov] target {target}: {pct:.2f}% "
                         f">= {floor:g}%")
    return worst, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, required=True)
    ap.add_argument("--xcode", action="store_true")
    ap.add_argument("--spm-glob", default=DEFAULT_SPM_GLOB)
    ap.add_argument("--dd", default=DEFAULT_DD, help="derived-data root (xcode)")
    ap.add_argument("--floors-json", default=os.environ.get("GOH_COV_FLOORS_JSON", ""),
                    help="per-target floors, same schema as coverage_gate.sh")
    args = ap.parse_args()

    if args.xcode:
        records, why = load_xcode(args.dd)
    else:
        records, paths = load_spm(args.spm_glob)
        if paths and not records:
            # Payloads exist but none yielded measurable files (unreadable
            # JSON, or every entry has total_lines 0). Refuse with the count
            # rather than aggregating an empty record set into a fake 100%.
            why = (f"{len(paths)} payload(s) matched {args.spm_glob} but "
                   f"none contained measurable files — corrupt codecov JSON?")
        else:
            why = None if paths else (
                f"no codecov payloads match {args.spm_glob} — was 'swift "
                f"test --enable-code-coverage' run?")

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
        print(f"→ [swift_cov] OK — {pct:.1f}% >= {floor:g}% "
              f"({len(per_file)} file(s))")
        return 0

    print(f"✗ [swift_cov] {pct:.1f}% is under the {floor:g}% floor:", file=sys.stderr)
    for n, p_, c, t in per_file[:20]:
        print(f"    {p_:6.1f}%  {c:>6}/{t:<6}  {n}", file=sys.stderr)
    if len(per_file) > 20:
        print(f"    … and {len(per_file) - 20} more", file=sys.stderr)
    print("\n  Write tests for what is uncovered; do not lower the floor.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
