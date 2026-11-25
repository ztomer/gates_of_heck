#!/usr/bin/env python3
"""Run each house checker against a corpus taken from the REAL estate, and require it to go red.

    check_estate_corpus.py              # the sweep
    check_estate_corpus.py --list       # name the corpora without building them
    check_estate_corpus.py --probe      # prove this gate can go red

WHY THIS EXISTS (SUPERSOTA R3). This is our single most repeated failure: a check written against
a fixture invented beside it, green across a dozen cases, and blind to the shape the real estate
actually has. Two, both in one session, both found against a real file rather than by reading:

  * `check_no_empty_assert.py`'s receiver pattern omitted `"`, so every emptiness assert whose
    receiver carried a string literal was skipped. Found by a real site in `storage-server` --
    `assert!(is_metadata(".DS_Store"))`.
  * a consumer's gluetun check read the LAST `image:` in a compose file, so it judged gluetun's
    pin by a different service's image. Found on the live host: 40-odd services per file.

Both fixtures had exactly one service and one image -- the shape the author had imagined. **A
fixture cannot disagree with the assumption that produced it.**

WHAT IS ACTUALLY CHECKABLE HERE, and this is the whole mechanism: for each declared checker, take
a REAL subtree from a REAL consumer repo, copy it (never run in place -- these are other people's
working trees), plant one violation inside it, and require the checker to go RED and NAME THE
PLANTED FILE. The plant goes into a real file of the real language, so the surrounding real code
is what the checker has to see through -- which is the entire difference from a fixture.

THE DIFFERENTIAL, which is why this is not just "plant and check". The same plant is ALSO run
against a MINIMAL one-file corpus. Three outcomes, all meaningful:

  real red, minimal red   the checker is right, and the fixture was not misleading anyone.
  real red, minimal GREEN the checker needed the real corpus to see it. Fine -- and reported,
                           because it means the fixture's proof was weaker than it looked.
  real GREEN              THE FAILURE. Either the shape is a blind spot, or -- the shape R3 is
                           about -- the author's fixture caught something the real estate does
                           not have, and the gate has been reading green on reality.

A corpus below `MIN_FILES` is REFUSED, never used: a "real corpus" that quietly degraded to one
file is the failure this gate exists to catch, reproduced inside the gate.

WHAT THIS IS NOT. It does not decide which checkers deserve an entry -- that judgement is a
person's, made once per checker and recorded in `ESTATE` with the reason. It does not prove a
checker CORRECT, only that it still refuses a violation in the shape the estate really has; that
is the claim R3 makes and the only one that is checkable without a live system. The cross-repo
survey that measures coverage (`games/game_asset_factory/tools/check_gate_calibration.py`) is a
different question and stays there.

SUPERSOTA R3: every declared house checker, handed a REAL subtree copied out of a real consumer
repo with one violation planted inside it, must still go red and name the planted file. Six
entries, each with the corpus and the reason it was chosen — the `check_no_empty_assert` entry
replays the 2026-10-02 receiver bug in the exact shape it failed in (a string literal INSIDE the
receiver). A corpus below a file floor, a corpus that is not CLEAN under the checker before the
plant lands (so a red cannot be credited to the plant), and a plant that would land in a stub are
all refusals. Copies, never runs in place: these are other people's working trees. `--probe` proves
a checker that cannot fail is caught.
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
import os
import shutil
import subprocess
import sys
import tempfile
import time

from _goh_tmp import goh_tmp  # the ONE temp parent (GOH.FSCHURN3)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _estate_cache
import _estate_corpus_io
from _estate_corpus_io import (  # noqa: F401  (re-exported: tests and probe call them here)
    STORE_ROOTS,
    consumer_exclude,
    corpus_files,
    materialise,
    plant_in,
    resolve_root,
    run_checker,
    store_dir,
    takes_exclude,
)
from _estate_table import ESTATE  # the table, split out by concern; tests read it here
from _gitutil import foreign_repo_env
from _ordered_pool import in_order

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted, err, info, ok, warn

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import not_applicable

HERE = os.path.dirname(os.path.abspath(__file__))
# The gates checkout whose `goh.sh` runs the native checks: GOH_DIR when the caller says (a copy of
# this file in another tree, as the empty-scope sweep makes), else the checkout this file is in.
GOH = os.environ.get("GOH_DIR") or os.path.dirname(HERE)
REPO = os.path.dirname(HERE)

# A corpus below this is not an estate, it is a fixture wearing a real repo's name. Measured: the
# smallest subtree here is 7 files, so 5 is loose but not decorative -- the point is that a
# corpus which lost its bulk REFUSES instead of quietly proving less.
MIN_FILES = 5

# ...and the planted file must be a real file with this many lines ALREADY IN IT. A plant that
# lands in a two-line stub has not been judged by the estate; it has been judged by the estate's
# FILENAME. Reported on every pass, so "real corpus" is a measurement rather than a claim.
MIN_TARGET_LINES = 10


def judge(entry, bad):
    """One entry, both corpora. Returns 'verified' | 'unavailable' | 'blind'."""
    source = resolve_root(entry["root"])
    if source is None or not os.path.isdir(source):
        where = (
            f"in {os.path.join(store_dir(), STORE_ROOTS)}" if source is None else "on this machine"
        )
        warn(f"{entry['checker']}: {entry['root']} is not {where} — NOT verified")
        return "unavailable"
    files = corpus_files(source, entry["scope"])
    if len(files) < MIN_FILES:
        where = "/".join(entry["scope"]) if entry["scope"] != (".",) else ""
        err(
            f"{entry['checker']}: the corpus at {entry['root']}/{where} has {counted(len(files), 'file')}, "
            f"below the floor of {MIN_FILES}. Refusing to count a fixture as an estate."
        )
        bad.append(entry["checker"])
        return "blind"
    # Judged as its owner's gate judges it: a path the owner exempts is not the estate for that
    # check (app_updates' vendored crate, 2026-10-08, carried a dead anchor its own gate exempts).
    exclude = consumer_exclude(source)
    target, _ = plant_in(files, entry["ext"], entry["plant"], exclude)
    if target is None:
        err(f"{entry['checker']}: no tracked {entry['ext']} file under that corpus to plant into")
        bad.append(entry["checker"])
        return "blind"
    with open(os.path.join(source, target), encoding="utf-8", errors="replace") as handle:
        existing = sum(1 for _ in handle)
    if existing < MIN_TARGET_LINES:
        err(
            f"{entry['checker']}: the plant would land in {target}, which holds {counted(existing, 'line')} "
            f"of the estate -- below the floor of {MIN_TARGET_LINES}. A violation in a stub is "
            f"judged by the estate's FILENAME."
        )
        bad.append(entry["checker"])
        return "blind"

    ident = _estate_cache.current_identity(os.path.abspath(__file__), GOH)
    key = _estate_cache.entry_key(ident, entry, source, files)
    if seen := _estate_cache.reusable(key, ident, entry):  # nothing it depends on moved
        ok(f"{seen['line']} -- verified {int(time.time() - seen['at'])}s ago, inputs unchanged")
        return "verified"
    with tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-estate.") as td:
        real = os.path.join(td, "real")
        minimal = os.path.join(td, "minimal")
        materialise(source, files, real)
        materialise(source, [target], minimal)

        # BEFORE the plant: the corpus must be CLEAN under this checker. Without this the sweep
        # cannot tell "the plant was refused" from "something else in the estate was already
        # refused, and the output happened to mention the file" -- a red that is not attributable
        # to what caused it. Found by replaying the 2026-10-02 receiver bug: the sweep reported
        # 6/6 green while the checker was blind to the plant, because the corpus carried a finding
        # of its own and the output named the file either way.
        pristine_rc, pristine_out = run_checker(entry["checker"], real, entry["args"], exclude)

        for corpus in (real, minimal):
            with open(os.path.join(corpus, target), "a", encoding="utf-8") as handle:
                handle.write(entry["plant"])
        real_rc, real_out = run_checker(entry["checker"], real, entry["args"], exclude)
        min_rc, _ = run_checker(entry["checker"], minimal, entry["args"], exclude)

    named = target in real_out
    if pristine_rc != 0:
        err(
            f"{entry['checker']}: the corpus at {entry['root']} is NOT clean under this checker "
            f"(exit {pristine_rc}), so a plant's verdict there would not be attributable to the "
            f"plant. First finding:"
        )
        for line in [ln for ln in pristine_out.strip().splitlines() if ln.strip()][:3]:
            info(f"    {line}")
        bad.append(entry["checker"])
        return "blind"
    if real_rc != 1 or not named:
        err(
            f"{entry['checker']}: a violation planted in {counted(len(files), 'real file')} from "
            f"{entry['root']} was NOT caught"
            + (f" (exit {real_rc})" if real_rc != 1 else " (exit 1, but it did not name the file)")
        )
        for line in real_out.strip().splitlines()[:6]:
            info(f"    {line}")
        bad.append(entry["checker"])
        return "blind"
    if min_rc != 1:
        # Not a failure. Worth saying out loud: the fixture would have been a WEAKER proof than
        # the corpus, which is the opposite of the R3 failure and just as invisible.
        info(
            f"{entry['checker']}: caught in the real corpus but NOT in a one-file fixture — the "
            f"estate was load-bearing"
        )
    line = (
        f"{entry['checker']}: red on a plant inside {target} ({existing} lines of real code, "
        f"{counted(len(files), 'real file')} from {os.path.basename(entry['root'])})"
        + ("" if min_rc == 1 else " -- and GREEN on the bare one-file fixture")
    )
    ok(line)
    _estate_cache.record(key, line)
    return "verified"


def _judged(entry, bad):
    state = judge(entry, mine := [])
    state = _estate_cache.shadowed(entry["checker"], state, lambda: judge(entry, []))
    bad.extend(mine)  # list.extend is atomic under the GIL; order is fixed by the replay below
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="name the corpora without building them")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)
    if args.probe:
        return probe()

    if args.list:
        for entry in ESTATE:
            info(f"{entry['checker']:32} {entry['root']}")
            info(f"{'':32} {entry['why']}")
        return 0

    bad = []
    states = in_order(lambda entry: _judged(entry, bad), ESTATE)
    verified = states.count("verified")
    unavailable = states.count("unavailable")
    if unavailable == len(ESTATE):
        # Not one corpus is on this machine: a sweep of nothing. On a hosted runner that is the
        # machine, said as such; anywhere else it is this gate's failure, never a green (CI.PUBLIC2).
        if not_applicable.declare(
            "the R3 estate corpus sweep", "the consumer repos it plants into"
        ):
            return not_applicable.EXIT
        err(f"none of the {len(ESTATE)} estate corpora is on this machine -- a sweep of nothing")
        return 1
    if bad:
        err(
            f"{counted(len(bad), 'checker')} did not refuse a violation planted in the real estate."
        )
        info("A checker that only works on the shape its author imagined is not a gate, and the")
        info("fixture beside it is the reason: a fixture cannot disagree with its own assumption.")
        return 1
    ok(
        f"{verified}/{counted(len(ESTATE), 'checker')} went red on a plant inside real estate corpora"
        + (f"; {unavailable} not on this machine, named above" if unavailable else "")
    )
    return 0


def probe():
    """Three cases: a real estate that is green, a checker that CANNOT fail, and a corpus that
    degraded to a fixture. The second is the whole point of this file.

    Two of the three plant into the first REAL entry's corpus. Where it is absent, a hosted runner
    says those two are NOT APPLICABLE, still runs the third, and exits not_applicable.EXIT; any
    other machine runs all three and the absent corpus fails them, as it always did (CI.PUBLIC2)."""

    saved, home, blind_dir = ESTATE, _estate_corpus_io.HERE, ""
    if not os.path.isdir(resolve_root(saved[0]["root"]) or "") and not_applicable.declare(
        "check_estate_corpus --probe (a checker that cannot fail; a real entry verifies)",
        f"its first real corpus, {saved[0]['root']}",
    ):
        if not _thin_refused({**saved[0], "args": (), "why": "probe"}):
            err("check_estate_corpus --probe: 1 case wrong")
            return 1
        return not_applicable.EXIT
    bad = 0
    try:
        # A checker that always passes: a sweep blind to it reports coverage it never measured.
        blind_dir = tempfile.mkdtemp(dir=goh_tmp(), prefix="goh-estate-blind.")
        blind = os.path.join(blind_dir, "check_cannot_fail.py")
        with open(blind, "w", encoding="utf-8") as handle:
            handle.write('"""Green whatever the tree says."""\nprint("clean")\n')
        _estate_corpus_io.HERE = os.path.dirname(blind)  # where run_checker finds a .py
        entry = {
            "checker": "check_cannot_fail.py",
            "root": saved[0]["root"],
            "scope": saved[0]["scope"],
            "ext": saved[0]["ext"],
            "plant": saved[0]["plant"],
            "args": (),
            "why": "probe",
        }
        findings = []
        state = judge(entry, findings)
        if state == "blind" and "check_cannot_fail.py" in findings:
            ok("probe: a checker that cannot fail is caught")
        else:
            err(
                f"probe: a checker that always exits 0 was reported as {state} — the sweep is blind"
            )
            bad += 1

        # A corpus below the floor must be REFUSED, not used: a real repo's name on one file is
        # the failure this gate exists to catch, reproduced inside the gate.
        if not _thin_refused(entry):
            bad += 1

        _estate_corpus_io.HERE = home
        if judge(saved[0], []) != "verified":
            err("probe: the first real estate entry did not verify — the sweep is broken")
            bad += 1
        else:
            ok("probe: a real estate entry verifies end to end")
    finally:
        globals()["ESTATE"] = saved
        _estate_corpus_io.HERE = home
        shutil.rmtree(blind_dir, ignore_errors=True)  # it leaked once per probe run (2026-10-06)

    if bad:
        err(f"check_estate_corpus --probe: {counted(bad, 'case')} wrong")
        return 1
    ok("check_estate_corpus --probe: a checker that cannot fail, and a corpus that is a fixture")
    return 0


def _thin_refused(entry):
    """True when a one-file corpus under a real entry's shape is REFUSED, never used."""
    with tempfile.TemporaryDirectory(dir=goh_tmp(), prefix="goh-estate.") as td:
        thin = os.path.join(td, "thin")
        os.makedirs(os.path.join(thin, "src"))
        with open(os.path.join(thin, "src", "lib.rs"), "w", encoding="utf-8") as handle:
            handle.write("fn main() {}\n")
        for args in (
            ("init", "-q"),
            ("config", "user.email", "c@e.invalid"),
            ("config", "user.name", "c"),
            ("add", "-A"),
        ):
            subprocess.run(
                ["git", "-C", thin, *args],
                capture_output=True,
                env=foreign_repo_env(),
                stdin=subprocess.DEVNULL,
                check=False,
            )
        findings = []
        judge(
            {**entry, "checker": "check_thin_corpus.py", "root": thin, "scope": ("src",)},
            findings,
        )
    if findings:
        ok("probe: a corpus below the floor is refused, not used")
        return True
    err("probe: a one-file 'estate' was accepted — the gate can prove a fixture")
    return False


if __name__ == "__main__":
    sys.exit(main())
