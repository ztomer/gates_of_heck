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

An IMPROVED count is the same debt, smaller. swiftlint's own --baseline
treats "currently complexity is 26" against a baselined 27 as NEW, so the
ratchet refused exactly the shrink it exists for (ZeroThunder 2026-10-17:
extracting a function out of a grandfathered draw() failed the build twice,
and the only way through was hand-editing the baseline). Here, after exact
matches are spent, a leftover live violation pairs with a leftover baselined
one of the same file and rule whose reason has the same WORDS (numbers
masked) and whose every number is >= the live one. It passes, with a
re-record nudge naming old -> new. A larger number in any position stays
new, and the message names the baselined value it grew from. Every rule
swiftlint ships with a count in its reason is an upper bound, so "smaller"
means "better"; a raised limit is a larger number and stays new.

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
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

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
    s = raw[len(FILE_URL_SCHEME) :] if raw.startswith(FILE_URL_SCHEME) else raw
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


NUMBER = re.compile(r"\d+(?:\.\d+)?")


def reason_shape(reason: str) -> tuple[str, tuple[float, ...]]:
    """("...complexity is #", (10.0, 27.0)): the words, and the numbers in order."""
    return NUMBER.sub("#", reason), tuple(float(n) for n in NUMBER.findall(reason))


class Reconciliation(NamedTuple):
    new: list[dict]  # must be empty for the gate to pass
    stale: list[tuple[str, str, str]]  # baselined keys that no longer occur
    improved: list[tuple[dict, str]]  # (live violation, the baselined reason it shrank from)
    grew_from: dict[int, str]  # id(new violation) -> the baselined reason it grew from


def reconcile(baseline_entries: list, live_violations: list, root: Path) -> Reconciliation:
    """Exact matches first, then improvements; see the module docstring."""
    tolerated = Counter(match_key(violation_of(e), root) for e in baseline_entries)
    unmatched: list[dict] = []
    for v in live_violations:
        k = match_key(v, root)
        if tolerated[k] > 0:
            tolerated[k] -= 1
        else:
            unmatched.append(v)

    def candidates(k: tuple[str, str, str]) -> list[tuple[str, str, str]]:
        words, _ = reason_shape(k[2])
        return [
            b for b in tolerated.elements() if b[:2] == k[:2] and reason_shape(b[2])[0] == words
        ]

    fresh: list[dict] = []
    improved: list[tuple[dict, str]] = []
    grew_from: dict[int, str] = {}
    for v in unmatched:
        k = match_key(v, root)
        nums = reason_shape(k[2])[1]
        same_words = candidates(k)
        fits = [b for b in same_words if all(n <= m for n, m in zip(nums, reason_shape(b[2])[1]))]
        if fits:
            # The tightest fit, so a looser entry stays free for a later, larger violation.
            best = min(fits, key=lambda b: reason_shape(b[2])[1])
            tolerated[best] -= 1
            improved.append((v, best[2]))
        else:
            fresh.append(v)
            if same_words:
                grew_from[id(v)] = min(same_words, key=lambda b: reason_shape(b[2])[1])[2]
    return Reconciliation(fresh, list(tolerated.elements()), improved, grew_from)


def describe_change(old_reason: str, new_reason: str) -> str:
    """'27 -> 26' for each number that moved."""
    old, new = reason_shape(old_reason)[1], reason_shape(new_reason)[1]
    return ", ".join(f"{_num(a)} -> {_num(b)}" for a, b in zip(old, new) if a != b)


def _num(x: float) -> str:
    return str(int(x)) if x.is_integer() else str(x)


def load_json_list(path: Path, what: str, swiftlint_rc: int = 0) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        die(f"{what} not found: {path}")
    except json.JSONDecodeError as exc:
        die(f"{what} is not valid JSON (swiftlint rc={swiftlint_rc}): {path} ({exc})")
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
    ap.add_argument(
        "--root", default=".", type=Path, help="repo root for path normalization (default: cwd)"
    )
    ap.add_argument(
        "--swiftlint-rc", type=int, default=0, help="swiftlint's own exit status (diagnostics only)"
    )
    args = ap.parse_args()

    if not args.baseline.exists():
        die(f"baseline not found: {args.baseline}")

    baseline = [violation_of(e) for e in load_json_list(args.baseline, "baseline")]
    report = load_json_list(args.report, "swiftlint report", swiftlint_rc=args.swiftlint_rc)
    bad = [e for e in report if not isinstance(e, dict)]
    if bad:
        die(
            "swiftlint report entries are not objects "
            f"({len(bad)} bad, swiftlint rc={args.swiftlint_rc})"
        )

    root = args.root.resolve()
    got = reconcile(baseline, report, root)

    if got.new:
        print("\u2717 swift gate: NEW lint violation(s) not in the baseline:")
        lines = []
        for v in got.new:
            line = describe_new(v)
            if id(v) in got.grew_from:
                line += f"  (grew: {describe_change(got.grew_from[id(v)], v.get('reason', ''))})"
            lines.append(line)
        for line in sorted(lines):
            print(f"  {line}")
        print(f"  {len(got.new)} new (baseline tolerates {sum(1 for _ in baseline)} listed)")
        return 1

    if got.improved:
        print(
            f"\u26a0 {len(got.improved)} baselined violation(s) shrank —",
            "re-record the baseline to lock the gain in (never grow it):",
        )
        for v, was in sorted(got.improved, key=lambda p: describe_new(p[0])):
            print(f"  {describe_new(v)}  ({describe_change(was, v.get('reason', ''))})")

    if got.stale:
        print(
            f"\u26a0 {len(got.stale)} baselined violation(s) no longer occur —",
            "re-record the baseline to shrink it (never grow it)",
        )

    if got.improved or got.stale:
        return 0

    if baseline or report:
        print("\u2713 all reported violations are baselined")
    return 0


if __name__ == "__main__":
    sys.exit(main())
