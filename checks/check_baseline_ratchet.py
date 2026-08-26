#!/usr/bin/env python3
"""Verify a baseline of ceilings can shrink but never grow.

One check, one name, for the ratchet that was being re-implemented per repo
and drifting: monitor's loc_baseline.txt (value TAB key lines), necrohand's
*_baseline.json and ZeroThunder's magic_literals_baseline.json (JSON dicts)
all encode the same contract — listed entries are CEILINGS. A value may fall
(the point of the exercise); a value that rises above its recorded ceiling,
or a NEW key appearing in current output, is growth and fails the gate.

Baseline formats are auto-detected:
    JSON        {"key": 123, ...}                     values must be numbers
    line format "123<TAB>key" or "123 key" per line   # comments allowed

Current values come from --current PATH (same file format as the baseline)
or --current-from-command 'cmd' (must PRINT in the same format). Both sides
go through the same parser, so a repo can switch formats without changing
the gate.

Exit codes: 0 pass · 1 violation · 2 precondition missing (with reason).

    check_baseline_ratchet.py --baseline loc_baseline.txt \
        --current-from-command 'scripts/count_loc.sh'
    check_baseline_ratchet.py --baseline magic_literals.json --current now.json
    check_baseline_ratchet.py --baseline loc_baseline.txt --record   # bless

--record rewrites the baseline from current values — after printing exactly
what changed, so the diff is reviewed, never silent. Only ever run it when a
shrink is landing; running it to absorb growth is how a ratchet dies.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
from killtree import run_captured  # noqa: E402


class PreconditionError(Exception):
    """The gate cannot run at all (missing file, unparseable input...)."""


def _reject_nonfinite(values: dict) -> None:
    """A non-finite value is a precondition failure wherever it appears —
    never compared. nan-vs-anything is always False (a NaN current would
    silently PASS), and inf-as-current vs a finite ceiling would 'fail as a
    violation' by accident of comparison rather than by policy."""
    bad = sorted(k for k, v in values.items()
                 if isinstance(v, float) and not math.isfinite(v))
    if bad:
        raise PreconditionError(
            f"non-finite values (NaN/Infinity) are not measurable — "
            f"keys {bad[:5]}")


# Strict ASCII numeric grammar for the line format. Python's int()/float()
# silently accept '1_0' (== 10) and Arabic-Indic digits ('١٢' == 12) — a
# baseline file is machine-compared truth, so anything outside this grammar
# must be a named precondition failure, never a quietly-different number.
_ASCII_NUMBER = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


def _line_value(value_s: str, origin: str, lineno: int) -> float:
    """Parse one line-format value: strict ASCII grammar first, then a real
    float conversion (huge literals exceed float range and are named).
    Non-finite spellings ('inf', 'nan') fall through to _reject_nonfinite,
    which names them with its own message."""
    if _ASCII_NUMBER.fullmatch(value_s):
        try:
            return float(value_s)
        except OverflowError:
            raise PreconditionError(
                f"{origin}:{lineno}: value exceeds float range: "
                f"{value_s[:40]!r}") from None
    try:
        probe = float(value_s)
    except ValueError:
        probe = None
    if probe is not None and not math.isfinite(probe):
        return probe
    raise PreconditionError(
        f"{origin}:{lineno}: value is not a plain ASCII number: {value_s!r}")


def parse(text: str, origin: str) -> dict[str, float]:
    """Parse either supported format into {key: number}.

    Values are NORMALIZED to finite floats here, so every downstream
    formatting ({v:g} in main/_record) works on the same representable
    domain the comparisons do."""
    stripped = text.strip()
    if not stripped:
        raise PreconditionError(f"{origin}: empty — nothing to verify")
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise PreconditionError(f"{origin}: looks like JSON but does not "
                                    f"parse ({exc})") from exc
        if not isinstance(data, dict):
            raise PreconditionError(f"{origin}: JSON must be an object "
                                    f"mapping key to number")
        bad = [k for k, v in data.items() if isinstance(v, bool)
               or not isinstance(v, (int, float))]
        if bad:
            raise PreconditionError(
                f"{origin}: non-numeric values for keys {sorted(bad)[:5]}")
        entries: dict[str, float] = {}
        huge = []
        for k, v in data.items():
            try:
                entries[str(k)] = float(v)
            except OverflowError:
                huge.append(k)  # JSON ints are unbounded; floats are not
        if huge:
            raise PreconditionError(
                f"{origin}: value(s) too large to measure (float range "
                f"exceeded) for keys {sorted(huge)[:5]}")
        _reject_nonfinite(entries)
        return entries

    out: dict[str, float] = {}
    for lineno, line in enumerate(stripped.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        raw = line.split("\t", 1) if "\t" in line else line.split(None, 1)
        if len(raw) != 2:
            raise PreconditionError(
                f"{origin}:{lineno}: expected '<value><TAB><key>' or "
                f"'<value> <key>', got: {line!r}")
        out[raw[1].strip()] = _line_value(raw[0].strip(), origin, lineno)
    _reject_nonfinite(out)
    return out


def load_current(args) -> dict[str, float]:
    if args.current_from_command is not None:
        # run_captured: own session + whole-group kill on timeout — a
        # background child of a timed-out command must not outlive the gate.
        proc = run_captured(
            args.current_from_command, shell=True, timeout=args.timeout)
        if proc.returncode != 0:
            raise PreconditionError(
                f"current-from-command exited {proc.returncode}: "
                f"{proc.stderr.strip()[:400]}")
        return parse(proc.stdout, "--current-from-command output")
    path = Path(args.current)
    if not path.is_file():
        raise PreconditionError(f"--current {path}: no such file")
    return parse(path.read_text(encoding="utf-8"), str(path))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Shrink-only ratchet: baselines are ceilings.")
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--current", help="path to current values (same format)")
    ap.add_argument("--current-from-command",
                    help="shell command printing current values (same format)")
    ap.add_argument("--allow-new-keys", action="store_true",
                    help="keys absent from the baseline do not fail the gate")
    ap.add_argument("--record", action="store_true",
                    help="rewrite the baseline from current values")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds allowed for --current-from-command")
    args = ap.parse_args()

    if (args.current is None) == (args.current_from_command is None):
        print("✗ [ratchet] pass exactly one of --current / "
              "--current-from-command", file=sys.stderr)
        return 2

    base_path = Path(args.baseline)
    if not base_path.is_file():
        print(f"✗ [ratchet] precondition missing: baseline "
              f"{base_path} does not exist\n"
              f"\n  Create it from today's truth (e.g. with --record), then\n"
              f"  commit it — the committed ceiling is what the gate enforces.",
              file=sys.stderr)
        return 2

    try:
        baseline = parse(base_path.read_text(encoding="utf-8"),
                         str(base_path))
        current = load_current(args)
    except PreconditionError as exc:
        print(f"✗ [ratchet] precondition missing: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"✗ [ratchet] precondition missing: current-from-command timed "
              f"out after {args.timeout}s", file=sys.stderr)
        return 2

    grew = sorted(
        (k for k in current
         if k not in baseline and not args.allow_new_keys))
    rose = sorted(
        (k for k in baseline
         if k in current and current[k] > baseline[k]))
    shrank = {
        k: (baseline[k], current[k])
        for k in baseline & current.keys() if current[k] < baseline[k]
    }
    vanished = sorted(k for k in baseline if k not in current)

    if args.record:
        _record(base_path, baseline, current, rose, grew, vanished)
        return 0

    if rose or grew:
        print(f"✗ [ratchet] {len(rose)} ceiling(s) exceeded, "
              f"{len(grew)} new key(s) — shrink-only:", file=sys.stderr)
        for k in rose:
            print(f"    {k}: {baseline[k]:g} -> {current[k]:g}  "
                  f"(+{current[k] - baseline[k]:g})", file=sys.stderr)
        for k in grew:
            print(f"    {k}: NEW at {current[k]:g} "
                  f"(use --allow-new-keys to permit)", file=sys.stderr)
        print("\n  Fix the regression, or — only if the new size is genuinely\n"
              "  intended — re-record the ceiling in the same commit, so the\n"
              "  growth is reviewed rather than absorbed.", file=sys.stderr)
        return 1

    detail = ""
    if shrank or vanished:
        parts = []
        if shrank:
            parts.append(", ".join(
                f"{k} {b:g}-> {c:g}" for k, (b, c) in sorted(shrank.items())))
        if vanished:
            parts.append("vanished: " + ", ".join(vanished))
        detail = f" ({'; '.join(parts)})"
    print(f"→ [ratchet] OK — {len(baseline)} entr{'y' if len(baseline) == 1 else 'ies'}"
          f" within ceilings{detail}")
    return 0


def _serialize(baseline_path: Path, current: dict[str, float]) -> str:
    """Write back in the SAME format the baseline came in."""
    text = baseline_path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        return json.dumps(current, indent=2, sort_keys=True) + "\n"
    lines = []
    for key in sorted(current):
        sep = "\t" if any("\t" in l for l in text.splitlines()) else " "
        lines.append(f"{current[key]:g}{sep}{key}")
    return "\n".join(lines) + "\n"


def _record(base_path: Path, baseline, current, rose, grew, vanished) -> None:
    changed = len(rose) + len(grew) + len(vanished) + sum(
        1 for k in baseline.keys() & current.keys()
        if baseline[k] != current[k])
    print(f"→ [ratchet] recording {changed} change(s) to {base_path}:")
    for k in rose:
        print(f"    {k}: {baseline[k]:g} -> {current[k]:g}")
    for k in sorted(baseline.keys() & current.keys()):
        if baseline[k] > current[k]:
            print(f"    {k}: {baseline[k]:g} -> {current[k]:g} (shrink)")
    for k in grew:
        print(f"    {k}: NEW at {current[k]:g}")
    for k in vanished:
        print(f"    {k}: removed (no longer produced)")
    base_path.write_text(_serialize(base_path, current), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
