# D-0012 — main's land.sh drives every queued land; one gate, the tip's; main's code reports on the steps a lane changed

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
  * CORRECTED (GOH.ONEGATE, 2026-11-25): main never judged the steps. `.gatesrc` GOH_CI_STEPS are
    `./gates/...` run in the tip's tree, so the second gate under main's runner (option 3, below)
    ran the lane's own step files. A probe of that gate over a lane that weakened its step and
    added what the step refuses: exit 0, and main's file run by hand over the same tree exit 1; a
    lane that dropped the step from the list: exit 0, the step absent. This record's proof ran
    main's file by path (`GOH_LAND_GATE='bash "$GOH_DIR/gates/..."'`), which no step of goh's list
    does. 284 of 314 landed logs of 7 days ran both gates; the second cost 16 s (p50) and was red
    in 0 of 321 lands of 14 days. What option 3 did judge is main's RUNNER (`local_ci.sh`).
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
     gate runs under main tip's gates first, then under the tip's own. TAKEN 2026-11-17; its first
     half stands, its second judged nothing of a step (corrected above).
  4. One gate a land under the tip's code, and a report-only judge: main's version of each step
     whose closure the lane changed, over the lane's tree, its verdict kept beside the tip's.
     TAKEN 2026-11-25 (GOH.ONEGATE, owner's ruling "1").
  5. The same judge, refusing. Not taken: its false-red rate is unmeasured on live lands. The rows
     of 4 measure it (`python3 lib/land_judge.py report`). A replay of 10 gate-changing lands of
     2026-11-24/25 (at load 55-85, 2 workers) cost p50 320 s a land, max 578 s, and had 3 would-be
     reds, all false: a lane that taught a check something and needed it in the same land
     (GOH.CONFIGMD's `docs/config/`, GOH.FSEVQUIET's empty estate, GOH.MAPGEN's SQL shape). Two
     more reds, the release canary's, were green when run again.
- **Ruling:** Options 3's first half and 4. `tools/landq.py` `land_script` is always the kit's
  `tools/land.sh` (main's export), for the enqueue pre-step, the lander and the batch; the lander
  re-execs when a land changes `tools/land.sh` as it does for `tools/landq.py` and `lib/`. A land
  runs one gate, under the tip's gates, which key the goh-landed line. After it, outside the land
  lock (main's versions cost more than the 16 s they replace), `lib/land_judge.py` runs main's
  version of each step the lane changed, names a would-be red, a step the lane broke, deleted or
  dropped from the list, and refuses nothing; what it could not judge is `unavailable`. It is spare
  work and a sample: a step runs only with `goh capacity`'s room and no land on its way
  (`lib/land_judge_room.py`), a land without room for 24 h is `unjudged`, and after
  GOH_LAND_JUDGE_SAMPLE judged lands it stops until `land_judge.py continue`. A
  consumer's land runs the kit's gates as before.
- **Falsifier:** `tests/test_land_driver.py`: the weakened-land.sh lane is refused at enqueue and
  red in the lander; a lane that weakens a step lands after one gate, and main's version of the
  step is then red beside the tip's green, in a row. `tests/test_land_judge_steps.py`: a weakened,
  a broken, a deleted and a dropped step are each named, and an unchanged step is not run again.
- **Re-open when:** 20 gate-changing lands are judged: `python3 lib/land_judge.py report` gives the false-red rate that option 5 waits on; or a weakening is found that rides on what the
  judge does not read: the lane's other `.gatesrc` keys and `GOH_LAND_PRE`, the tests a lane edits
  (`rust_gate.sh` is judged with its test group deferred), a step's proof recorded by the lane's
  own code, and a hand-run `bash tools/land.sh`, which is the lane's own code by definition (the
  queue is the authority).
- **Lives in:** `tools/landq.py` (`land_script`, `code_digest`), `tools/land.sh` (`land_roots`),
  `lib/land_gate.sh` (`land_gate`, `land_judge_owe`), `lib/land_judge_owe.py`,
  `lib/land_judge.py`, `lib/land_judge_room.py`,
  `lib/land_judge_steps.py`, `lib/land_judge_rows.py`, `tests/test_land_driver.py`, `tests/test_land_identity.py`
