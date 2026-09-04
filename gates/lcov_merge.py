#!/usr/bin/env python3
"""lcov merger for coverage_gate.sh's rust mode — ONE export PER TEST TARGET,
unioned here with CGU-hash normalization.

Extracted verbatim from coverage_gate.sh (which sat at the 500-line cap) so
the merge is unit-testable directly. Hardened while moving, four proven
defects fixed; behavior for WELL-FORMED input is byte-identical:

  (a) Parts are opened encoding="utf-8-sig": a BOM'd part used to carry the
      BOM into its first "SF:" record, dropping that whole file's records
      from the merge (false GREEN 100% over a true 75%).
  (b) FN format detection parses fields[1] as an int instead of trusting the
      field COUNT: a two-field record whose mangled name contains a comma
      ("FN:10,_Z4other,foo") looked three-field and crashed on int(name).
      RESIDUAL, documented: a genuinely corrupt three-field record whose END
      field is not numeric re-reads as a two-field name — accepted, since
      real producers emit one of the two live formats.
  (c) Malformed DA/FNDA/FN records raise LcovError → exit 2 NAMING file and
      line, instead of a ValueError/IndexError traceback colliding with the
      below-floor exit-1 semantics.
  (d) The merge itself (CGU strip, span forgiveness, uncovered-line report)
      is unchanged; see coverage_gate.sh's lineage comment.

Usage: lcov_merge.py --floor N <parts-dir>   (parts named part-*.info)
Exit codes: 0 pass | 1 below floor / nothing coverable | 2 malformed input.
"""

import argparse
import glob
import os
import re
import sys
from collections import defaultdict


def normalize(mangled: str) -> str:
    """Strip the CGU hash so duplicate instantiations group together."""
    return re.sub(r"Cs[0-9A-Za-z]+_", "Cs_", mangled)


class LcovError(Exception):
    """Malformed lcov input; carries file:line."""


def _int_or_none(text: str):
    try:
        return int(text)
    except ValueError:
        return None


def parse_part(pf, line_best, fn_best, fn_start, fn_end):
    """Fold one lcov part into the shared accumulators."""
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
                    # TWO formats, both live: cargo-llvm-cov emits two-field
                    # FN:start,name; geninfo emits three-field FN:start,end,
                    # name. A name may itself contain commas (mangled
                    # symbols), so three-field is detected by fields[1]
                    # parsing as an INT — never by field count alone.
                    fields = line[3:].split(",")
                    start = int(fields[0])
                    end = _int_or_none(fields[1]) if len(fields) >= 3 else None
                    if end is not None:
                        key = (cur, normalize(",".join(fields[2:])))
                        fn_end.setdefault(key, end)
                    else:
                        key = (cur, normalize(",".join(fields[1:])))
                    fn_start.setdefault(key, start)
                elif line.startswith("FNDA:"):
                    cnt, name = line[5:].split(",", 1)
                    key = (cur, normalize(name))
                    fn_best[key] = max(fn_best[key], int(cnt))
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
        raw = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        print(f"✗ [coverage] cannot read floors file {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        return None
    if any(k in raw for k in ("targets", "file_floor", "tolerance", "exempt")):
        return {"file_floor": float(raw["file_floor"]) if "file_floor" in raw else None,
                "tolerance": float(raw.get("tolerance", 0.0)),
                "exempt": dict(raw.get("exempt", {}))}
    return {"file_floor": None, "tolerance": 0.0, "exempt": {},
            "targets": {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}}


def merge(parts, floor, include_re="", floors_json=None, marker_ceiling=None):
    """The union + floor decision. Returns process exit code."""
    inc_pat = None
    if include_re:
        try:
            inc_pat = re.compile(include_re)
        except re.error as exc:
            print(f"✗ [coverage] bad --include regex: {exc}", file=sys.stderr)
            return 2
    line_best = defaultdict(int)   # (file, line) -> max count across exports
    fn_best = defaultdict(int)     # (file, norm_name) -> max FNDA
    fn_start = {}                  # (file, norm_name) -> first start line
    fn_end = {}                    # (file, norm_name) -> declared end line, when carried

    try:
        for pf in parts:
            parse_part(pf, line_best, fn_best, fn_start, fn_end)
    except LcovError as exc:
        print(f"✗ [coverage] {exc}", file=sys.stderr)
        return 2
    if inc_pat is not None:
        line_best = {k: v for k, v in line_best.items() if inc_pat.search(k[0])}
        fn_best = {k: v for k, v in fn_best.items() if inc_pat.search(k[0])}
        fn_start = {k: v for k, v in fn_start.items() if inc_pat.search(k[0])}
        fn_end = {k: v for k, v in fn_end.items() if inc_pat.search(k[0])}

    # Executed spans per file: a function spans from its start line to the line
    # before the next function's start; a line inside a span whose FNDA>0 anywhere
    # is covered even if its own DA row shows 0 (duplicate zero-count clone).
    spans = defaultdict(list)      # file -> [(start, end, executed)]
    by_file_fn = defaultdict(dict)
    for (f, name), st in fn_start.items():
        by_file_fn[f][name] = st
    max_da = defaultdict(int)
    for (f, ln) in line_best:
        max_da[f] = max(max_da[f], ln)
    for f, names in by_file_fn.items():
        ordered = sorted(set(names.values()))
        for name, st in names.items():
            nxt = min([s for s in ordered if s > st], default=None)
            end = (nxt - 1) if nxt is not None else 10**9
            if (f, name) in fn_end:
                # Three-field record carries a real span end: bound forgiveness by
                # min(declared end, last measured line). Without this, an executed
                # fn forgives every uncovered module-tail line after it — the
                # phantom-clone span ran to the next fn or to infinity.
                end = min(end, fn_end[(f, name)], max_da.get(f, 10**9))
            # Two-field records KEEP the open-ended span unchanged: bounding those
            # differently would move live cargo-llvm-cov consumers' coverage
            # numbers, which is explicitly out of scope for this fix.
            spans[f].append((st, end, fn_best.get((f, name), 0) > 0))

    missed = defaultdict(list)
    total = len(line_best)
    covered = 0
    for (f, ln), cnt in line_best.items():
        if cnt > 0:
            covered += 1
            continue
        for st, end, executed in spans.get(f, []):
            if st <= ln <= end and executed:
                covered += 1
                break
        else:
            missed[f].append(ln)

    if total == 0:
        print("✗ [coverage] no coverable lines found in any lcov part")
        return 1

    pct = round(100.0 * covered / total, 2)
    floors = _load_floors(floors_json) if floors_json else None
    per_file_fail = False
    if floors and floors.get("file_floor") is not None:
        ff = floors["file_floor"]
        exempt = floors.get("exempt", {})
        per_tot = defaultdict(int)
        per_cov = defaultdict(int)
        for (f, ln), cnt in line_best.items():
            per_tot[f] += 1
            cov = 1 if cnt > 0 else 0
            if not cov:
                for st, end, executed in spans.get(f, []):
                    if st <= ln <= end and executed:
                        cov = 1
                        break
            per_cov[f] += cov
        below = []
        for f, tot in per_tot.items():
            if f in exempt:
                continue
            cur = 100.0 * per_cov[f] / tot if tot else 100.0
            if cur + 1e-9 < ff - floors.get("tolerance", 0.0):
                below.append((f, per_cov[f], tot, cur))
        if below:
            print(f"✗ [coverage] {len(below)} file(s) below per-file floor {ff:g}%", file=sys.stderr)
            for f, cov, tot, cur in sorted(below, key=lambda r: r[3]):
                print(f"    {cur:5.1f}%  {tot-cov:4d} uncovered  {f}", file=sys.stderr)
            per_file_fail = True
        stale = sorted(set(exempt) - set(per_tot))
        if stale:
            print(f"✗ {len(stale)} exemption(s) stale (file gone).", file=sys.stderr)
            for s in stale:
                print(f"    {s}", file=sys.stderr)
            return 1
    if marker_ceiling and os.path.exists(marker_ceiling):
        try:
            import json as _jm
            max_forgiven = int(_jm.load(open(marker_ceiling, encoding="utf-8"))["max_forgiven_lines"])
            if 0 < max_forgiven:
                print(f"→ [coverage] forgiveness 0 < ceiling {max_forgiven} — lower it.")
        except Exception as exc:
            print(f"✗ [coverage] cannot read ceiling {marker_ceiling}: {exc}", file=sys.stderr)
            return 2
    if floor is None and floors is not None:
        return 1 if per_file_fail else 0
    if floor is None:
        print("✗ [coverage] no floor supplied", file=sys.stderr)
        return 2
    below = pct < floor
    print(("✗" if below else "✓") + f" [coverage] {pct}% of coverable lines (floor {floor:g}%, {covered}/{total} lines)")
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
    ap.add_argument("--floor", type=float, required=False, default=None, help="minimum LINE coverage percent")
    ap.add_argument("--include", default="", help="positive filter regex")
    ap.add_argument("--floors-json", dest="floors_json", default="", help="per-file floors JSON")
    ap.add_argument("--marker-ceiling", dest="marker_ceiling", default="", help="forgiveness ceiling JSON")
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
    return merge(parts, args.floor, args.include, args.floors_json or None, args.marker_ceiling or None)


if __name__ == "__main__":
    sys.exit(main())
