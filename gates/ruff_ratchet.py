#!/usr/bin/env python3
"""ruff_ratchet.py -- the repo's declared ruff rule set is a gate, its debt a per-rule count that only falls.

A repo that declares a rule set and gates none of it has a linter it reads by eye: ZoneWM's
ruff.toml selected PLW1510 alone and named "ruff's defaults read 841 findings here" in a comment,
and monitor's pyproject declared its rules while no step ran them (G17c). Fixing every finding in
one commit is the right end and rarely the right first step, and the two ways to defer it hide the
debt: a baseline of fingerprints (no reader sees the findings again) or a blanket ignore (the rule
stops existing). This is the third way: the rule set runs whole, and what it still reports is held
to a COUNT per rule, in `ruff_ratchet.json`:

    {"BLE001": 36, "UP031": 32}

The verdict, per rule:
  * more findings than its count, or a rule with findings and no count: RED, naming every finding
    of that rule -- new debt is refused at the commit that brings it;
  * FEWER findings than its count: RED as well, "lower it" -- a count left above the truth is room
    for the next regression to hide in, so the table always says exactly what is left, and the
    only direction it ever moves is down (`--lower` writes the measured counts, refusing while
    anything rose);
  * a count at zero, or for a rule with no findings: stale, delete it.
Nothing is hidden: `--list` prints every remaining finding, and the passing line says how many are
left under how many rules.

    ruff_ratchet.py                      # judge (cwd = the repo, its own ruff config)
    ruff_ratchet.py --list               # every finding still owed
    ruff_ratchet.py --lower              # record a shrink (refused while anything rose)
    ruff_ratchet.py --seed               # first adoption only: refused if the table exists

Exit 0 pass, 1 a finding, 2 a precondition (ruff did not run, the table is missing or malformed).
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
import contextlib
import json
import os
import re
import shlex
import subprocess
import sys
from collections import Counter
from pathlib import Path

TABLE = "ruff_ratchet.json"
RULE = re.compile(r"^[A-Z]+[0-9]+$")
# Findings listed per rule in a red verdict before the rest are counted, not printed.
SHOWN = 40


class Precondition(Exception):
    """The gate cannot judge: ruff did not run, or the table is not a table."""


def findings(ruff: str, paths: list[str]) -> list[dict]:
    """ruff's findings under the repo's own config, as its JSON report."""
    cmd = [*shlex.split(ruff), "check", "--no-cache", "--exit-zero", "--output-format", "json"]
    try:
        r = subprocess.run(
            [*cmd, *(paths or ["."])],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise Precondition(f"ruff did not run ({' '.join(cmd)}): {exc}") from exc
    # --exit-zero: any non-zero is ruff refusing (bad config, unknown rule), never "findings".
    if r.returncode != 0:
        raise Precondition(f"ruff exited {r.returncode}: {r.stderr.strip()[:400]}")
    try:
        report = json.loads(r.stdout or "[]")
    except ValueError as exc:
        raise Precondition(f"ruff's JSON report does not parse: {exc}") from exc
    if not isinstance(report, list):
        raise Precondition("ruff's JSON report is not a list of findings")
    return report


def rule_of(finding: dict) -> str:
    # A syntax error has no code; it is a finding like any other, under its own name.
    return str(finding.get("code") or "syntax-error")


def where(finding: dict) -> str:
    loc = finding.get("location") or {}
    name = str(finding.get("filename", "?"))
    with contextlib.suppress(
        ValueError
    ):  # ruff reports absolute paths; the reader wants the repo's
        name = os.path.relpath(name) if os.path.isabs(name) else name
    return (
        f"{name}:{loc.get('row', '?')}:{loc.get('column', '?')}: "
        f"{rule_of(finding)} {finding.get('message', '')}"
    )


def load_table(path: Path) -> dict[str, int]:
    """The ceilings. A missing or malformed table is a precondition, never an empty one."""
    if not path.is_file():
        raise Precondition(
            f"{path} is missing -- the ratchet's counts are the gate's subject "
            f"(first adoption: --seed, then commit it)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Precondition(f"{path} does not parse: {exc}") from exc
    if not isinstance(data, dict):
        raise Precondition(f"{path} is not an object of rule -> count")
    table: dict[str, int] = {}
    for rule, count in data.items():
        if not RULE.match(rule) and rule != "syntax-error":
            raise Precondition(f"{path}: {rule!r} is not a ruff rule code")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise Precondition(f"{path}: {rule} has count {count!r}, not a whole number")
        table[rule] = count
    return table


def write_table(path: Path, counts: Counter) -> None:
    body = {rule: counts[rule] for rule in sorted(counts) if counts[rule] > 0}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def judge(table: dict[str, int], report: list[dict]) -> tuple[list[str], list[str]]:
    """(rises, stale): lines for rules over their count, and counts above the truth."""
    counts = Counter(rule_of(f) for f in report)
    rises: list[str] = []
    stale: list[str] = []
    for rule in sorted(counts):
        ceiling = table.get(rule)
        if ceiling is not None and counts[rule] <= ceiling:
            continue
        head = (
            f"{rule}: {counts[rule]} finding(s), and no count -- a rule the repo had cleared"
            if ceiling is None
            else f"{rule}: {counts[rule]} finding(s) against a count of {ceiling}"
        )
        rises.append(head)
        mine = [f for f in report if rule_of(f) == rule]
        rises.extend(f"    {where(f)}" for f in mine[:SHOWN])
        if len(mine) > SHOWN:
            rises.append(f"    ... and {len(mine) - SHOWN} more (--list)")
    for rule in sorted(table):
        if table[rule] == 0:
            stale.append(f"{rule}: a count of 0 -- delete the entry")
        elif counts[rule] < table[rule]:
            stale.append(f"{rule}: {counts[rule]} finding(s), count {table[rule]} -- lower it")
    return rises, stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--table", default=TABLE, help=f"the per-rule counts (default {TABLE})")
    ap.add_argument("--ruff", default="ruff", help="how to run ruff (default: ruff)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="print every remaining finding")
    mode.add_argument("--lower", action="store_true", help="record a shrink")
    mode.add_argument("--seed", action="store_true", help="first adoption: write the table")
    ap.add_argument("paths", nargs="*", help="what ruff reads (default: .)")
    args = ap.parse_args(argv)
    path = Path(args.table)
    try:
        report = findings(args.ruff, args.paths)
        if args.seed:
            if path.exists():
                print(
                    f"✗ [ruff-ratchet] {path} exists -- --seed is first adoption only; "
                    f"a shrink is --lower",
                    file=sys.stderr,
                )
                return 2
            counts = Counter(rule_of(f) for f in report)
            write_table(path, counts)
            print(
                f"→ [ruff-ratchet] seeded {path}: {sum(counts.values())} finding(s) "
                f"under {len(counts)} rule(s) -- commit it, then only lower it"
            )
            return 0
        table = load_table(path)
    except Precondition as exc:
        print(f"✗ [ruff-ratchet] {exc}", file=sys.stderr)
        return 2
    if args.list:
        for f in sorted(report, key=where):
            print(where(f))
        print(f"→ [ruff-ratchet] {len(report)} finding(s) owed")
        return 0
    rises, stale = judge(table, report)
    if args.lower:
        if rises:
            print(
                "✗ [ruff-ratchet] --lower refused: a rule ROSE, and lowering would absorb it",
                file=sys.stderr,
            )
            for line in rises:
                print(line, file=sys.stderr)
            return 1
        write_table(path, Counter(rule_of(f) for f in report))
        print(f"→ [ruff-ratchet] {path} lowered to the measured counts")
        return 0
    if rises or stale:
        if rises:
            print(
                "✗ [ruff-ratchet] new findings under the repo's declared rule set -- fix them "
                "(the count never rises):",
                file=sys.stderr,
            )
            for line in rises:
                print(f"  {line}", file=sys.stderr)
        if stale:
            print(
                f"✗ [ruff-ratchet] {path} is above the truth -- run --lower in this commit, "
                f"so the count can only fall:",
                file=sys.stderr,
            )
            for line in stale:
                print(f"  {line}", file=sys.stderr)
        return 1
    print(
        f"✓ [ruff-ratchet] OK -- {len(report)} finding(s) owed under {len(table)} counted "
        f"rule(s), none new"
        if report
        else "✓ [ruff-ratchet] OK -- the declared rule set is clean"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
