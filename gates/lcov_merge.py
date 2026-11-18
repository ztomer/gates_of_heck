#!/usr/bin/env python3
"""lcov merger for coverage_gate.sh's rust mode — every lcov part (one instrumented run of
all targets, plus the --external part) unioned here, each line judged by its own best count.

Extracted verbatim from coverage_gate.sh (which sat at the 500-line cap) so
the merge is unit-testable directly. Hardened while moving, four proven
defects fixed:

  (a) Parts are opened encoding="utf-8-sig": a BOM'd part used to carry the
      BOM into its first "SF:" record, dropping that whole file's records
      from the merge (false GREEN 100% over a true 75%).
  (b) FN records parse in both live formats (two-field FN:start,name from
      cargo-llvm-cov; three-field FN:start,end,name from geninfo), a mangled
      name's commas included, and a non-numeric start is malformed input.
  (c) Malformed DA/FNDA/FN records raise LcovError → exit 2 NAMING file and
      line, instead of a ValueError/IndexError traceback colliding with the
      below-floor exit-1 semantics.
  (d) NO SPAN FORGIVENESS (GOH.SPANFORGIVE, 2026-11-18). A zero-count line
      inside any executed function's span used to count as covered, for
      "phantom misses from zero-count clones" of instantiated generics. The
      producer has no such phantom: llvm-cov's lcov DA rows are the union over
      every instantiation of the file (a clone appears only as its own FN /
      FNDA:0 record; tests/test_lcov_union_falsifier.py pins it on the real
      toolchain), and a clone's zero row in another part or record never lowers
      a line's best count here. What the span forgave was a line NO
      instantiation ran -- an untaken arm of a called function -- so every
      Rust floor read high. A line is covered iff its best DA count is > 0.

Usage: lcov_merge.py --floor N <parts-dir>   (parts named part-*.info)
Exit codes: 0 pass | 1 below floor / nothing coverable | 2 malformed input.
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
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted, err  # the count meets its noun (GOH.HEDGEOWN)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _lcov_floor_maps as floor_maps  # GOH.CRATEFLOOR: crate_floors / file_floors


class LcovError(Exception):
    """Malformed lcov input; carries file:line."""


def parse_part(pf, line_best):
    """Fold one lcov part's line counts into `line_best` ((file, line) -> best count).

    FN and FNDA records are validated, not kept: a line's coverage is its own DA count, already
    the union over every instantiation (module docstring, (d))."""
    cur = None
    das = []
    with open(pf, encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            try:
                if line.startswith("SF:"):
                    cur = line[3:]
                elif cur is None:
                    continue
                elif line.startswith("FN:") and "," in line[3:]:
                    int(line[3:].split(",", 1)[0])  # FN:start,name | FN:start,end,name
                elif line.startswith("FNDA:"):
                    cnt, _name = line[5:].split(",", 1)
                    int(cnt)
                elif line.startswith("DA:") and cur:
                    p = line[3:].split(",")
                    das.append((cur, int(p[0]), int(p[1])))
            except (ValueError, IndexError):
                raise LcovError(f"{pf}:{lineno}: malformed lcov record: {line!r}")
    for f, ln, cnt in das:
        line_best[(f, ln)] = max(line_best[(f, ln)], cnt)


def _load_floors(path):
    import json

    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        # REFUSED, not dropped: this returned None and the run went on to pass on --floor alone,
        # its per-file check silently gone (ztools wrote _note strings instead of comments for
        # exactly this reason, 2026-10-05).
        print(f"✗ [coverage] cannot read floors file {path}: {exc}", file=sys.stderr)
        return {"refused": True}
    if not isinstance(raw, dict):
        print(f"✗ [coverage] floors file {path} is not a JSON object", file=sys.stderr)
        return {"refused": True}
    wrapped = any(
        k in raw
        for k in ("targets", "file_floor", "tolerance", "exempt", "crate_floors", "file_floors")
    )
    targets = (
        dict(raw.get("targets") or {})
        if wrapped
        else {k: v for k, v in raw.items() if isinstance(v, (int, float))}
    )
    if targets:
        # PER-TARGET floors are a swift-mode feature (gates/coverage_swift.py applies them). Here
        # both shapes were read and NOTHING applied them -- a rust repo writing {"mycrate": 100}
        # was gated on nothing (2026-10-06). The rust mode measures one workspace part, so a
        # per-target floor is not measurable: refuse, naming them, rather than look like a gate.
        names = ", ".join(sorted(targets))
        print(
            f"✗ [coverage] {path}: per-target floors ({names}) cannot be applied in the rust "
            "mode -- it measures the workspace as one part. Use --floor N, or file_floor for "
            "per-file floors.",
            file=sys.stderr,
        )
        return {"refused": True}
    # GOH.CRATEFLOOR: the two maps that lower ONE crate or file below the uniform floor.
    crate_floors = floor_maps.load_entries(raw.get("crate_floors"), "crate_floors", path)
    file_floors = floor_maps.load_entries(raw.get("file_floors"), "file_floors", path)
    if crate_floors is None or file_floors is None:
        return {"refused": True}
    return {
        "file_floor": float(raw["file_floor"]) if "file_floor" in raw else None,
        "tolerance": float(raw.get("tolerance", 0.0)),
        "exempt": dict(raw.get("exempt", {})),
        "crate_floors": crate_floors,
        "file_floors": file_floors,
    }


def crate_of(path: str) -> str:
    """The crate a source file is of: the directory after `crates/`; "-" outside one."""
    m = re.search(r"(?:^|/)crates/([^/]+)/", path)
    return m.group(1) if m else "-"


def write_summary(dest, per_crate) -> None:
    """Each crate's [covered, coverable] lines, the floor's own count (GOH.COVCARRY2): the claim a
    land records and the idle full run compares (lib/prof_carry.py). Written whole (a rename)."""
    tmp = f"{dest}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"crates": {k: list(v) for k, v in sorted(per_crate.items())}}, fh)
    os.replace(tmp, dest)


def merge(parts, floor, include_re="", floors_json=None, marker_ceiling=None, summary=None):
    """The union + floor decision. Returns process exit code; `summary`, a path for the per-crate
    counts (write_summary)."""
    inc_pat = None
    if include_re:
        try:
            inc_pat = re.compile(include_re)
        except re.error as exc:
            print(f"✗ [coverage] bad --include regex: {exc}", file=sys.stderr)
            return 2
    line_best = defaultdict(int)  # (file, line) -> max count across exports

    try:
        for pf in parts:
            parse_part(pf, line_best)
    except LcovError as exc:
        print(f"✗ [coverage] {exc}", file=sys.stderr)
        return 2
    if inc_pat is not None:
        line_best = {k: v for k, v in line_best.items() if inc_pat.search(k[0])}

    missed = defaultdict(list)
    total = len(line_best)
    covered = 0
    per_crate = defaultdict(lambda: [0, 0])
    for (f, ln), cnt in line_best.items():
        per_crate[crate_of(f)][1] += 1
        if cnt > 0:
            covered += 1
            per_crate[crate_of(f)][0] += 1
        else:
            missed[f].append(ln)
    if summary and total:
        write_summary(summary, per_crate)

    if total == 0:
        print("✗ [coverage] no coverable lines found in any lcov part")
        return 1

    floors = _load_floors(floors_json) if floors_json else None
    if floors and floors.get("refused"):
        return 2
    lines_of = defaultdict(int)
    for f, _ln in line_best:
        lines_of[f] += 1
    per_file = {f: (n - len(missed.get(f, ())), n) for f, n in lines_of.items()}
    map_fails: list[str] = []
    rest = None
    if floors:
        map_fails, rest = floor_maps.judge_crates(
            floors.get("crate_floors"),
            floor,
            per_file,
            missed,
            floor_maps.crates_of(per_file),
            os.getcwd(),
        )
    if rest is not None:  # the uniform floor judges the crates no entry lowers
        covered = sum(per_file[f][0] for f in rest)
        total = sum(per_file[f][1] for f in rest)
        missed = {f: v for f, v in missed.items() if f in set(rest)}
    pct = round(100.0 * covered / total, 2) if total else 100.0
    per_file_fail = False
    if floors and (floors.get("file_floors") or {}) and floors.get("file_floor") is None:
        map_fails.append("file_floors lowers `file_floor`, and none is set")
    if floors and floors.get("file_floor") is not None:
        ff = floors["file_floor"]
        file_floors = floors.get("file_floors") or {}
        map_fails += floor_maps.not_lowering(file_floors, ff, "file_floors")

        # An exempt key names its file absolutely or RELATIVE TO THE PROJECT (the merger runs in
        # it): lcov's SF is absolute, so an absolute-only key held at one checkout path and was
        # "stale" in a push gate's export or on another machine (ztools, 2026-10-05).
        def _norm(path):
            return os.path.realpath(
                path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
            )

        exempt_by_path = {_norm(k): k for k in floors.get("exempt", {})}
        own_by_path = {_norm(k): v[0] for k, v in file_floors.items()}
        gone = sorted(k for k in file_floors if not os.path.exists(_norm(k)))
        map_fails += [f"file_floors[{k!r}] names no file (stale: delete it)" for k in gone]
        below = []
        for f, (cov_f, tot) in per_file.items():
            if _norm(f) in exempt_by_path:
                continue
            cur = 100.0 * cov_f / tot if tot else 100.0
            own = own_by_path.get(_norm(f), ff)
            if cur + 1e-9 < own - floors.get("tolerance", 0.0):
                below.append((f, cov_f, tot, cur))
        if below:
            print(
                f"✗ [coverage] {counted(len(below), 'file')} below per-file floor {ff:g}%",
                file=sys.stderr,
            )
            for f, cov, tot, cur in sorted(below, key=lambda r: r[3]):
                print(f"    {cur:5.1f}%  {tot - cov:4d} uncovered  {f}", file=sys.stderr)
            per_file_fail = True
        measured = {_norm(f) for f in per_file}
        stale = sorted(k for n, k in exempt_by_path.items() if n not in measured)
        if stale:
            print(f"✗ {counted(len(stale), 'exemption')} stale (file gone).", file=sys.stderr)
            for name in stale:
                print(f"    {name}", file=sys.stderr)
            return 1
    for msg in map_fails:
        err(f"[coverage] {msg}")
    if map_fails:
        per_file_fail = True
    if marker_ceiling and os.path.exists(marker_ceiling):
        try:
            import json as _jm

            with open(marker_ceiling, encoding="utf-8") as handle:
                max_forgiven = int(_jm.load(handle)["max_forgiven_lines"])
            if max_forgiven > 0:
                print(f"→ [coverage] forgiveness 0 < ceiling {max_forgiven} — lower it.")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"✗ [coverage] cannot read ceiling {marker_ceiling}: {exc}", file=sys.stderr)
            return 2
    if floor is None and floors is not None:
        return 1 if per_file_fail else 0
    if floor is None:
        print("✗ [coverage] no floor supplied", file=sys.stderr)
        return 2
    below = pct < floor
    print(  # `↔` (tui `measure`): the measurement goh_step shows on a pass (GOH.COVSHOW)
        ("✗" if below else "↔")
        + f" [coverage] {pct}% of coverable lines (floor {floor:g}%, {covered}/{total} lines)"
    )
    if below:
        print("✗ [coverage] uncovered lines:")
        for f, lines in sorted(missed.items()):
            print(f"  {f}: {', '.join(map(str, lines))}")
    if per_file_fail:
        return 1
    return 1 if below else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("parts_dir", help="directory holding part-*.info exports")
    ap.add_argument(
        "--floor", type=float, required=False, default=None, help="minimum LINE coverage percent"
    )
    ap.add_argument("--include", default="", help="positive filter regex")
    ap.add_argument("--floors-json", dest="floors_json", default="", help="per-file floors JSON")
    ap.add_argument(
        "--marker-ceiling", dest="marker_ceiling", default="", help="forgiveness ceiling JSON"
    )
    ap.add_argument("--summary", default="", help="write each crate's covered/coverable lines")
    args = ap.parse_args(argv)
    if not args.include and os.environ.get("GOH_COV_INCLUDE_RE"):
        args.include = os.environ["GOH_COV_INCLUDE_RE"]
    if not args.floors_json and os.environ.get("GOH_COV_FLOORS_JSON"):
        args.floors_json = os.environ["GOH_COV_FLOORS_JSON"]
    if not args.marker_ceiling and os.environ.get("GOH_COV_MARKER_CEILING"):
        args.marker_ceiling = os.environ["GOH_COV_MARKER_CEILING"]
    if args.floor is None and not args.floors_json:
        ap.error("need --floor N or --floors-json PATH")
    parts = sorted(glob.glob(os.path.join(args.parts_dir, "part-*.info")))
    return merge(
        parts,
        args.floor,
        args.include,
        args.floors_json or None,
        args.marker_ceiling or None,
        args.summary or None,
    )


if __name__ == "__main__":
    sys.exit(main())
