"""A land records the gate identity of the commit it landed, the one every reader computes.

GOH.VERDICTKEYDRIFT. tools/land.sh runs from the head export of the branch tip it STARTED on
(gates/_from_head.sh); when main had moved, it rebased and gated the new tip -- but the gate, the
goh-landed line's identity and the loaded run's record key still came from the pre-rebase export.
b54b9d62 landed rebased from 380f48d0: its loaded verdict was recorded under 380f48d0's gates
(...-aa4f97f2.json), the ship computed b54b9d62's (...-7d4d1790) and refused a green tree. Here,
on a scratch clone of today's gates run from its export (no GOH_LIVE), main moves a gate file under
the branch, the land rebases, and the identity it records must be the one the push rule's reader
(lib/release_gate.py's land_identity against lib/gate_identity.py) computes from main's checkout.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _todays_gates import _clean_checkout_of_todays_gates
from conftest import hermetic_env, native_goh_path
from timing_bounds import patience


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True, env=hermetic_env(drop_git=True),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _reader_key(main: Path, mode: str, env: dict) -> list[str]:
    """What the push rule compares a goh-landed line with, computed in main's checkout."""
    code = ("import sys; sys.path.insert(0, sys.argv[1] + '/lib');"
            "import gate_identity, release_gate; from pathlib import Path; m = Path(sys.argv[1]);"
            "print(release_gate.land_identity(m, sys.argv[3]),"
            " gate_identity.key(sys.argv[2], m))")  # fmt: skip
    tip = _git(main, "rev-parse", "HEAD")
    out = subprocess.run([sys.executable, "-c", code, str(main), mode, tip], env=env, cwd=main,
                         capture_output=True, text=True, check=True, timeout=patience(60),
                         stdin=subprocess.DEVNULL).stdout.split()  # fmt: skip
    return out


def test_a_rebased_land_records_the_landed_tips_identity(tmp_path: Path) -> None:
    main = _clean_checkout_of_todays_gates(tmp_path, rust=False)
    wt = tmp_path / "lane"
    _git(main, "worktree", "add", "-q", "-b", "feature-under-land", str(wt))
    (wt / "lane.txt").write_text("the lane's change\n")
    _git(wt, "add", "lane.txt")
    _git(wt, "commit", "-q", "--no-verify", "-m", "lane")
    # main moves a GATE byte under the lane: the rebased tip's gates are not the branch tip's
    lib = main / "lib" / "gate_identity.py"
    # One byte more on a line it has: the file may sit at the 500-line cap, and the land's pre-step
    # judges the rebased tree (GOH.PREPARITY), so a 501st line would be a red main, not a moved one.
    lib.write_text(
        lib.read_text().replace("    sys.exit(main())\n", "    sys.exit(main())  # moved\n", 1)
    )
    assert "# moved" in lib.read_text(), "the fixture must move a gate byte"
    _git(main, "commit", "-q", "--no-verify", "-am", "main moved a gate")
    # No pre-step (GOH_LAND_PRE set empty): the subject is the identity a rebased land records, and
    # the declared pre-step ran the structural gates and the diff's tests on the fixture's tree --
    # ~90 of this test's ~100 s at load 20-30, at its 60 s ceiling at low load, so any load
    # turned a land red (GOH.CEILINGHEADROOM, 2026-11-20). The rebase it reads happens without it.
    env = hermetic_env(drop_git=True, GOH_LAND_GATE="true", GOH_LAND_PRE="")
    for k in ("GOH_LIVE", "GOH_LIVE_ROOT", "PYTEST_CURRENT_TEST", "PYTHONPATH"):
        env.pop(k, None)
    # GATES_OF_HECK_SUITE_RUNNER: no loaded run started behind this land
    env.update(GOH_DIR=str(main), GOH_HEAD_CACHE=str(tmp_path / "head-cache"),
               GOH_BIN=str(native_goh_path()),
               GATES_OF_HECK_SUITE_RUNNER=str(os.getpid()))  # fmt: skip
    r = subprocess.run(["bash", str(wt / "tools" / "land.sh")], cwd=wt, env=env, text=True,
                       capture_output=True, timeout=patience(300), stdin=subprocess.DEVNULL,
                       check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rebased feature-under-land" in r.stdout + r.stderr, "the fixture must rebase the land"
    # The reader runs the binary the land ran: an explicit GOH_BIN is keyed (GOH.PROVENIDENTITY).
    reader = hermetic_env(drop_git=True, GOH_LIVE="1", GOH_BIN=str(native_goh_path()))
    recorded, computed = _reader_key(main, "true", reader)
    assert recorded == computed, (
        f"goh-landed says {recorded[:16]}, the reader computes {computed[:16]}: the land keyed "
        f"the pre-rebase tip's gates\n{r.stdout}{r.stderr}"
    )
