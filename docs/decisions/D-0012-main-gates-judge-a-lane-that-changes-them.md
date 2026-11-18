# D-0012 — main's land.sh drives every queued land, and main's gates judge a lane that changes them

- **Question:** (GOH.PRESTEPHEAD, from GOH.PRESTEPSHARE) Which code judges a goh lane's land: the
  lane's own copy of `tools/land.sh`, its libs and its gates, or main's? GOH.PRESTEPSHARE's enqueue
  printed the share-scaled pre-step deadline its own change introduced before that change had
  landed: the enqueue ran the WORKTREE's land.sh. A lane that weakens land.sh, or a gate, is then
  judged by its own weakened copy and can pass itself.
- **Evidence:** (2026-11-17, read in the tree at b851b948, measured on main)
  * Every path that picked the gate code for a goh lane, before this record:
    `tools/landq.py` `land_script` returned the worktree's `tools/land.sh` when it had one, and
    both the enqueue pre-step (`lib/land_prestep.py`, `GOH_LAND_PRE_ONLY=1`) and the lander
    (`argv_for`, `lib/landq_batch.py` `Kit.land_script`) ran it; that script's C4 stanza
    (`gates/_from_head.sh` `goh_from_head`) re-ran it from the export of the LANE's HEAD, so
    `$GOH` was the lane's gates; the gate it ran was `$GOH/tools/gate_profile.sh .` (the lane's
    `gates/local_ci.sh`, `checks/`, `lib/`), and GOH.VERDICTKEYDRIFT's rebase step switched `$GOH`
    to the rebased tip's export, the lane's again. `GOH_LAND_GATE` and `GOH_LAND_PRE` are read from
    the lane's `.gatesrc`. `gates/_from_head.py` only re-runs a Python entry point from its own
    checkout's HEAD, which in a lane is the lane's. So no step of a queued goh land ran main's code.
  * Red on that rule (`tests/test_land_driver.py`): a lane whose `tools/land.sh` skips the pre-step
    was queued past a red pre-step set (exit 0), and one whose land.sh fast-forwards main without
    a gate landed under `GOH_LAND_GATE=false` (`landed`); a lane that rewrote a gate to `exit 0`
    and added the file that gate refuses landed by its own land.sh (exit 0, main moved).
  * Price: 111 of main's last 150 first-parent commits (74%) change the gate code (gates/ checks/
    lib/ tools/ tui/ crates/ Cargo.* rust-toolchain.toml .gatesrc), so the second run below is the
    common case for goh. Its steps whose closure (`lib/step_closure.py`) the lane did not change
    have the same proven key under either gates and are cache hits in the second run
    (`GOH_PROFILE_PROVEN=1`, GOH.PROVENCLOSURE); the rest re-run. Deciding costs two gate-identity
    keys (0.4 s each) and one export per commit, cached.
- **Options priced:**
  1. (a) Main's gates only: the tip's gate change never runs before main moves, and the land's
     verdicts are keyed under main's gates, so the ship (which runs the landed tip's) re-gates
     every gate-changing land in full after main already holds it. Rejected: the self-proof moves
     after the fast-forward.
  2. (b) The lane's copy only, as before, trusting review of the diff. Rejected: the risk itself.
  3. (b) with a re-judge: the queue drives with MAIN's land.sh (the lane's own is never run by the
     queue), and when the diff from main changes the gate identity's content or `.gatesrc`, the
     gate runs under main tip's gates first (the land gate string read from main's `.gatesrc`),
     then under the tip's own; a diff that changes neither runs one gate under the tip's (the same
     code). TAKEN.
- **Ruling:** (GOH.PRESTEPHEAD, 2026-11-17) Option 3. `tools/landq.py` `land_script` is always the
  kit's `tools/land.sh` (main's export), for the enqueue pre-step, the lander and the batch; the
  lander re-execs when a land changes `tools/land.sh` as it does for `tools/landq.py` and `lib/`.
  `tools/land.sh` `land_roots` decides, for a tree sharing the gates' root commit, whether main's
  gates judge too, and `land_gate` runs each; the tip's gates key the goh-landed line. A consumer's
  land runs the kit's gates as before.
- **Falsifier:** `tests/test_land_driver.py`: the weakened-land.sh lane is refused at enqueue and
  red in the lander; the weakened-gate lane is red under main's copy and main does not move, and a
  lane that edits the gate harmlessly runs main's gate, then its own, and lands.
- **Re-open when:** the second run's measured cost exceeds half a land's gate on the median of ten
  gate-changing lands (narrow it to the steps whose closure changed); or a weakening is found that
  rides on what this does not cover: the lane's `.gatesrc` step list and `GOH_LAND_PRE`, the
  tests a lane edits, and a hand-run `bash tools/land.sh`, which is the lane's own code by
  definition (the queue is the authority).
- **Lives in:** `tools/landq.py` (`land_script`, `code_digest`), `tools/land.sh` (`land_roots`,
  `land_gate`), `tests/test_land_driver.py`, `tests/test_land_identity.py`
