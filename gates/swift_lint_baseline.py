#!/usr/bin/env python3
"""Reconcile a live swiftlint JSON report against a baseline — the ratchet.

House ratchet semantics (matched against ZoneTilerWM's `make check-lint`,
which runs `swiftlint lint --baseline .swiftlint-baseline.json --strict`,
and probed against swiftlint 0.65.1 on 2026-08-25):

  1. Violations LISTED in the baseline are tolerated.
  2. NEW violations (not listed) fail, each named file:line rule reason.
  3. Listed violations that have VANISHED are never a failure — they mean the
     debt shrank and the baseline should be re-recorded (a printed nudge).

Match key — what makes a live violation "the same one" as a baselined entry.
Probed, not assumed (each row verified by mutating a real tree):

  file          YES  byte-identical violation in another file reports as new
  rule id       YES  obviously distinct
  reason        YES  changing the count in "currently it has N characters"
                     reports the violation as new
  line/column   NO   shifting code down a blank line stays tolerated
  severity      NO   warning->error threshold flips stay tolerated

So the key is (normalized file, rule id, reason), compared as MULTISETS: two
identical violations in one file are tolerated iff the baseline lists two.

Path normalization. SwiftLint's JSON reporter emits ABSOLUTE paths;
`--write-baseline` records paths relative to the invocation directory with
the leading "/" STRIPPED (yes — "var/folders/...", probed 2026-08-25), and
ZoneTilerWM's tools/relativize_lint_baseline.py rewrites baselines to
repo-relative form (also stripping a file:// scheme). Both sides are
normalized here to repo-root-relative strings so a baseline means the same
thing from any checkout directory.

Exit codes: 0 clean-or-nudge, 1 new violations (named), 2 unusable input
(missing/malformed baseline or report — with the swiftlint exit status named,
since a crashed swiftlint must fail the gate, not silently pass it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

FILE_URL_SCHEME = "file://"


def die(msg: str, code: int = 2) -> None:
    print(f"\u2717 {msg}", file=sys.stderr)
    sys.exit(code)


def violation_of(entry: object) -> dict:
    """Baseline entries wrap their violation ({text, violation}); reports don't."""
    if isinstance(entry, dict) and isinstance(entry.get("violation"), dict):
        return entry["violation"]
    if isinstance(entry, dict):
        return entry
    return {}


def normalize_path(raw: str, root: Path) -> str:
    """Any recorded form (absolute, file:// URL, leading-slash-stripped,
    repo-relative) -> one comparable string relative to `root`.

    Disambiguating repo-relative from swiftlint's leading-slash-stripped
    ABSOLUTE writer form must not depend on the file existing (a baselined
    file may have been deleted); it is structural: re-root the candidate at
    "/" and treat it as absolute iff it then lands inside `root`. Both landings
    are resolved through symlinks first — swiftlint writes paths under /var
    where the checkout may sit at /private/var (probed 2026-08-25).
    """
    s = raw[len(FILE_URL_SCHEME):] if raw.startswith(FILE_URL_SCHEME) else raw
    p = Path(s)
    rr = Path(root).resolve()
    if not p.is_absolute():
        restored = (Path("/") / p).resolve()
        p = restored if rr == restored or rr in restored.parents else rr / p
    p = p.resolve()
    try:
        return str(p.relative_to(rr))
    except ValueError:
        return str(p)  # outside this checkout: compare as-is, matches nothing


def match_key(violation: dict, root: Path) -> tuple[str, str, str]:
    loc = violation.get("location", {})
    file_raw = loc.get("file") or violation.get("file") or ""
    rule = violation.get("ruleIdentifier") or violation.get("rule_id") or ""
    return (normalize_path(file_raw, root), rule, violation.get("reason", ""))


def reconcile(
    baseline_entries: list, live_violations: list, root: Path
) -> tuple[list[dict], list[dict]]:
    """Return (new, stale). `new` must be empty for the gate to pass."""
    tolerated = Counter(
        match_key(violation_of(e), root) for e in baseline_entries
    )
    fresh: list[dict] = []
    for v in live_violations:
        k = match_key(v, root)
        if tolerated[k] > 0:
            tolerated[k] -= 1
        else:
            fresh.append(v)
    return fresh, list(tolerated.elements())


def load_json_list(path: Path, what: str, swiftlint_rc: int = 0) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"{what} not found: {path}")
    except json.JSONDecodeError as exc:
        die(f"{what} is not valid JSON (swiftlint rc={swiftlint_rc}): "
            f"{path} ({exc})")
    if not isinstance(data, list):
        die(f"{what} must be a JSON array: {path}")
    return data


def describe_new(v: dict) -> str:
    loc = v.get("location", {})
    file_raw = loc.get("file") or v.get("file") or "<unknown>"
    line = loc.get("line") or v.get("line") or "?"
    rule = v.get("rule_id") or v.get("ruleIdentifier") or "?"
    reason = v.get("reason", "")
    return f"{file_raw}:{line} {rule}: {reason}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--root", default=".", type=Path,
                    help="repo root for path normalization (default: cwd)")
    ap.add_argument("--swiftlint-rc", type=int, default=0,
                    help="swiftlint's own exit status (diagnostics only)")
    args = ap.parse_args()

    if not args.baseline.exists():
        die(f"baseline not found: {args.baseline}")

    baseline = [
        violation_of(e)
        for e in load_json_list(args.baseline, "baseline")
    ]
    report = load_json_list(args.report, "swiftlint report",
                            swiftlint_rc=args.swiftlint_rc)
    bad = [e for e in report if not isinstance(e, dict)]
    if bad:
        die("swiftlint report entries are not objects "
            f"({len(bad)} bad, swiftlint rc={args.swiftlint_rc})")

    root = args.root.resolve()
    fresh, stale = reconcile(baseline, report, root)

    if fresh:
        print("\u2717 swift gate: NEW lint violation(s) not in the baseline:")
        for v in sorted(describe_new(v) for v in fresh):
            print(f"  {v}")
        print(f"  {len(fresh)} new (baseline tolerates "
              f"{sum(1 for _ in baseline)} listed)")
        return 1

    if stale:
        print(f"\u26a0 {len(stale)} baselined violation(s) no longer occur —",
              "re-record the baseline to shrink it (never grow it)")
        return 0

    if baseline or report:
        print("\u2713 all reported violations are baselined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
