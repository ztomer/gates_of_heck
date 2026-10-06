"""The self-proof for `checks/_calibration.py`, split out so neither file crowds the cap.

Run from `check_probes_pass.py --probe`, which is what the sweep discovers. Every way the
calibration registry can lie is asserted red and the sound registry asserted clean: a key naming
no gate, a citation naming no file, a citation to a proof that does not run, a prover in no
commit, a prover that does not claim the gate, an entry that is both proven and unproven. A
checker that cannot fail is the thing this whole layer exists to prevent, and a calibration reader
that cannot fail would be the most ironic instance of it in the repo.
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

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _calibration  # noqa: E402
from _calibration import REGISTRY, foreign_repo_env, load, verify  # noqa: E402
from tui.lib import err, ok  # noqa: E402

PROVES = 'import sys\nif "--probe" in sys.argv:\n    print("clean")\nsys.exit(0)\n'
RAN = {"checks/check_prover.py", "checks/check_sound_gate.py"}
# (label, proven_by, known_unproven, unproven_why, ran, substring a finding must carry -- None
# means this case must come back CLEAN)
CASES = (
    (
        "a sound citation against a proof that ran is clean",
        {"sound_gate": "checks/check_sound_gate.py --probe: red on a violation"},
        (),
        {},
        RAN,
        None,
    ),
    (
        "a self-proof claim (no file named) is left alone",
        {"sound_gate": "in-selftest: three directions against a temp tree"},
        (),
        {},
        RAN,
        None,
    ),
    (
        "a key naming a gate that does not exist is caught",
        {"no_such_gate": "checks/check_absent.py --probe: red"},
        (),
        {},
        RAN,
        "does not exist",
    ),
    (
        "a key that names no gate at all, prose or not, is caught",
        {"phantom": "in-selftest: proven somehow"},
        (),
        {},
        RAN,
        "names no gate",
    ),
    (
        "a citation to a proof that did not run is caught",
        {"prover": "checks/check_prover.py --probe: red on a violation"},
        (),
        {},
        set(),
        "did not run to green",
    ),
    (
        "a citation to a gate that declares no self-proof is caught",
        {"quiet": "checks/check_quiet.py --probe: red on a violation"},
        (),
        {},
        RAN,
        "did not run to green",
    ),
    (
        "an entry that is both proven and known_unproven is caught",
        {"prover": "checks/check_prover.py --probe: red on a violation"},
        ("prover",),
        {"prover": "needs a built Swift package"},
        RAN,
        "both proven and known_unproven",
    ),
    (
        "a known_unproven entry with no reason is caught",
        {"sound_gate": "checks/check_sound_gate.py --probe: red"},
        ("sound_gate",),
        {},
        RAN,
        "no entry in unproven_why",
    ),
    (
        "a known_unproven entry WITH its reason is clean",
        {"sound_gate": "checks/check_sound_gate.py --probe: red"},
        ("check_swift_coverage",),
        {"check_swift_coverage": "needs a built Swift package"},
        RAN,
        None,
    ),
)


def _write(repo, rel, body):
    with open(os.path.join(repo, rel), "w", encoding="utf-8") as handle:
        handle.write(body)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
    ).returncode


def _cases(estate):
    """The table cases, against a fresh estate each time."""
    os.makedirs(os.path.join(estate, "checks"), exist_ok=True)
    for name in ("check_prover.py", "check_sound_gate.py", "check_swift_coverage.py"):
        _write(estate, os.path.join("checks", name), PROVES)
    # Declares no self-proof. A registry citing its --probe is the R7 shape exactly: the sweep
    # cannot see it (there is nothing to discover) while the registry calls it proven.
    _write(estate, "checks/check_quiet.py", '"""No self-proof here."""\nprint("clean")\n')
    bad = 0
    for label, entries, unproven, whys, ran, want in CASES:
        with open(os.path.join(estate, REGISTRY), "w", encoding="utf-8") as handle:
            json.dump(
                {"proven_by": entries, "known_unproven": list(unproven), "unproven_why": whys},
                handle,
            )
        found = verify(estate, load(estate), ran)
        if want is None and not found:
            ok(f"probe: {label}")
        elif want is None:
            err(f"probe: {label} -- wrongly reported {found}")
            bad += 1
        elif any(want in line for line in found):
            ok(f"probe: {label}")
        else:
            err(f"probe: {label} -- not reported (got {found})")
            bad += 1
    return bad


def _uncommitted(td):
    """A prover on disk but in NO COMMIT: the proof runs here and no clone can check it."""
    repo = os.path.join(td, "repo")
    os.makedirs(os.path.join(repo, "checks"))
    for name in ("check_prover.py", "check_sound_gate.py", "check_quiet.py"):
        _write(repo, os.path.join("checks", name), "print('clean')\n")
    with open(os.path.join(repo, REGISTRY), "w", encoding="utf-8") as handle:
        json.dump(
            {"proven_by": {"prover": "checks/check_prover.py --probe: red on a violation"}},
            handle,
        )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "probe@example.invalid")
    _git(repo, "config", "user.name", "probe")
    _git(repo, "add", "-f", REGISTRY)
    _git(repo, "commit", "-qm", "registry only")
    found = verify(repo, load(repo), {"checks/check_prover.py"})
    bad = 0
    if any("in NO COMMIT" in line for line in found):
        ok("probe: a citation to a prover that is in no commit is caught")
    else:
        err(f"probe: an UNCOMMITTED prover was accepted (got {found})")
        bad += 1
    _git(repo, "add", "-f", "checks")
    _git(repo, "commit", "-qm", "the provers themselves")
    if verify(repo, load(repo), {"checks/check_prover.py"}):
        err("probe: the same citation, now COMMITTED, was still reported -- the rule is wrong")
        bad += 1
    else:
        ok("probe: the same citation passes once the prover is committed")
    return bad


def _external(td, estate):
    """The other estate, with a prover that does not claim the gate.

    Both roots are rebound for the duration, so the case needs nothing of this machine's layout.
    """
    other = os.path.join(td, "other")
    os.makedirs(os.path.join(other, "tools"))
    os.makedirs(os.path.join(other, ".git"))
    prover = os.path.join("tools", "check_other_prover.py")
    _write(other, prover, '"""prover."""\nprint("clean")\n')
    with open(os.path.join(estate, REGISTRY), "w", encoding="utf-8") as handle:
        json.dump({"proven_by": {"sound_gate": "tools/check_other_prover.py -- red"}}, handle)
    saved = (_calibration.ESTATE_ROOTS, _calibration.ESTATE_OWNERS)
    _calibration.ESTATE_ROOTS = (other,)
    _calibration.ESTATE_OWNERS = {other: "the other estate"}
    bad = 0
    try:
        found = verify(estate, load(estate), RAN)
        if any("among what it proves" in line for line in found):
            ok("probe: a prover that does not claim the gate is caught")
        else:
            err(f"probe: a prover claiming nothing was accepted (got {found})")
            bad += 1
        _write(
            other,
            prover,
            '"""p."""\nimport sys\nif "--proves" in sys.argv:\n    print("sound_gate")\n',
        )
        if verify(estate, load(estate), RAN):
            err("probe: a SOUND external citation was wrongly reported")
            bad += 1
        else:
            ok("probe: a sound external citation passes")
    finally:
        _calibration.ESTATE_ROOTS, _calibration.ESTATE_OWNERS = saved
    return bad


def probe():
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        estate = os.path.join(td, "estate")
        bad += _cases(estate)
        bad += _uncommitted(td)
        bad += _external(td, estate)
    if bad:
        err(f"calibration probe: {bad} case(s) wrong -- the reader does not measure its claim")
        return 1
    ok("calibration probe: a claim naming no gate, no file, no commit or no proof goes red")
    return 0


if __name__ == "__main__":
    sys.exit(probe())
