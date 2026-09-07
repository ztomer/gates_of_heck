#!/usr/bin/env python3
"""Every file exempted from the line cap must still have a CEILING.

`GOH_LINE_EXCLUDE` exists for a cohesive module that is genuinely over the cap
and should not be split today. That is a reasonable exemption from the CAP. It
is not an exemption from having any bound at all -- but that is what it silently
became, because the shrink-only ratchet
(`checks/check_baseline_ratchet.py`) is a separate mechanism with a separate
list, and nothing ever compared the two.

Found in monitor on 2026-09-07: `crates/multitop/tests/event_loop_e2e.rs` was
619 lines, named in `GOH_LINE_EXCLUDE` (so the cap did not apply) and absent
from `tools/loc_baseline.txt` (so no ceiling applied). It could grow without
limit and no gate would ever say a word. The repo's own baseline header
asserted the opposite in prose -- "tests are held to the same 500-line cap" --
so the document read as though the hole did not exist.

THE DISTINCTION THIS CHECK RESTS ON: `GOH_EXCLUDE` covers vendored and
generated files, which are not ours to split and correctly have no ceiling;
`GOH_LINE_EXCLUDE` covers OUR files that are too long. Only the latter is
checked here.

But `GOH_LINE_EXCLUDE` is documented as "additive to the length check ONLY", so
a repo may legitimately use it for material that must stay emoji- and
secret-scanned while being exempt from the cap -- divoom-control names captured
vendor API docs there, which are not ours to split and whose size is not ours to
ratchet either. That is what `GOH_LINE_UNBOUNDED` is for: a second regex naming
the exemptions that need no ceiling, with the reason stated beside it in the
repo's own `.gatesrc`. Policy central, exemptions local. It is checked for
staleness like every other list -- a pattern matching nothing FAILS.

Enable it by pointing `GOH_LINE_BASELINE` at the repo's ratchet baseline. The
policy is central; the exemptions and the baseline stay local -- an
unconfigured repo is told the check is off rather than being passed silently.

    check_exclusion_has_ceiling.py --max 500 --line-exclude 're1|re2' \
        --baseline tools/loc_baseline.txt
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _gitutil import content_bytes, line_count, listed_files, repo_root  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import err, info, ok  # noqa: E402


def baseline_keys(path: Path) -> set[str]:
    """Paths carrying a ceiling. Same two formats check_baseline_ratchet reads."""
    text = path.read_text(errors="replace")
    keys: set[str] = set()
    if text.lstrip().startswith("{"):
        import json

        return set(json.loads(text))
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split("\t") if "\t" in line else line.split(None, 1)
        if len(parts) == 2:
            keys.add(parts[1].strip())
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, required=True)
    ap.add_argument("--line-exclude", default="")
    ap.add_argument(
        "--unbounded",
        default="",
        help="exemptions that legitimately need no ceiling (captured/vendored data)",
    )
    ap.add_argument("--baseline", required=True)
    args = ap.parse_args()

    root = repo_root()
    base = Path(root) / args.baseline
    if not base.exists():
        err(f"[ceiling] baseline {args.baseline} not found — fix GOH_LINE_BASELINE")
        return 2
    if not args.line_exclude:
        ok("[ceiling] OK — no GOH_LINE_EXCLUDE entries, so nothing is exempt from the cap")
        return 0

    rx = re.compile(args.line_exclude)
    unbounded = re.compile(args.unbounded) if args.unbounded else None
    have = baseline_keys(base)
    offenders, inspected, waived = [], 0, 0
    for rel in listed_files(root, False):
        if not rx.search(rel):
            continue
        if unbounded is not None and unbounded.search(rel):
            waived += 1
            continue
        inspected += 1
        # A file exempted from the cap but under it anyway needs no ceiling:
        # the cap would bind it if the exemption were removed.
        blob = content_bytes(root, rel, False)
        if blob is None:
            continue
        n = line_count(blob)
        if n > args.max and rel not in have:
            offenders.append((rel, n))

    if offenders:
        err(f"[ceiling] {len(offenders)} file(s) exempt from the cap with NO ceiling")
        for rel, n in offenders:
            info(
                f"{rel} ({n} lines) is in GOH_LINE_EXCLUDE, so the {args.max}-line "
                f"cap does not apply, and is absent from {args.baseline}, so no "
                f"ratchet ceiling applies either. It can grow without limit."
            )
        info(f"Fix: split it, or add '{offenders[0][1]} {offenders[0][0]}' to {args.baseline}.")
        return 1
    if unbounded is not None and waived == 0:
        err("[ceiling] GOH_LINE_UNBOUNDED matched 0 tracked files — it waives")
        info("    nothing. Prune it, or fix the pattern; a waiver for material")
        info("    that is gone reads exactly like one that is doing its job.")
        return 1
    if inspected == 0 and waived == 0:
        err("[ceiling] GOH_LINE_EXCLUDE matched 0 tracked files — the exemption")
        info("    list names paths that no longer exist. Prune it; an exemption for")
        info("    a file that is gone is indistinguishable from one that is working.")
        return 1
    tail = f", {waived} waived as unbounded by nature" if waived else ""
    ok(
        f"[ceiling] OK — {inspected} exempt file(s), each under the cap or "
        f"carrying a ceiling{tail}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
