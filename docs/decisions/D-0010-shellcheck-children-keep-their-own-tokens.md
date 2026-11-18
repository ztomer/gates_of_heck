# D-0010 — shellcheck children keep their own token pool; a gate slot counts gates, a token counts a child

- **Question:** after D-0008 every gate, land and (GOH.ADMITSUITE) hand-run suite comes in by one
  admission (`lib/admission.py` `admit`: memory, then a gate slot). The shellcheck tokens
  (`crates/goh/src/shellcheck_slots.rs`, `GOH_SHELLCHECK_SLOTS`, `~/.cache/goh/slots/shellcheck`)
  are a second counting semaphore beside it. Should they be folded into the gate slots?
- **Evidence:** the incident the tokens were built for (GOH.MEMADMIT, 2026-11-16): 128 shellcheck
  children holding 63.7 GiB, nearly all started inside admitted gates and the suite -- `goh
  shell-lint` ran one child per core per run, about eight runs at once. Measured 2026-11-17 on the
  64 GiB, 16-core Mac, one child at a time as `goh shell-lint` calls it (`-x
  --source-path=SCRIPTDIR --format=gcc`) over goh's 118 shell files: with the dataflow analysis on
  (severity `style`, below `warning`) median 76 MB, p90 912 MB, max 2006 MB per child
  (`gates/rust_gate.sh`, 10.9 s; 178 s summed); with it off (the default `warning`) the ten
  heaviest files 136-221 MB. Default tokens here: 8 (one per 8 GiB), so the children of every lint
  on the machine are bounded at 8 x 2.0 GB = 16 GB at worst, 1.8 GB at the default severity.
  `tests/test_shellcheck_slots.py`: four lint runs of six files each, every run saying it is
  inside a held gate slot, kept at most 3 children alive under a bound of 3, and 24 with the bound off.
- **Options priced:**
  1. A lint run takes a gate slot. Every lint that matters runs inside a gate or the suite, which
     already holds a slot, so it is NESTED and takes none (`lib/gate_slot.py` NESTING): the bound
     on the incident's population becomes nothing -- 16 children per run, unbounded runs, the
     63.7 GiB again. Rejected.
  2. Each child takes a gate slot. A gate holding one of the 2 slots cannot take another for its
     own children without deadlocking against the other gate (or nesting, which is option 1);
     and 2 is a count of gates (D-0009: ~5 cores each), not of 76 MB-2 GB children. Rejected.
  3. Ask memory per child (`lib/mem_admit.py`) instead of tokens. A reading before each child
     admits all 16 a run starts in the same instant (none has grown yet); it bounds a rate, not
     a population. Rejected.
  4. Keep the per-child token pool, sized by memory, held only around one child and nothing taken
     under it (the lock order's last machine rank, `shellcheck` 96). TAKEN.
- **Ruling:** (GOH.ADMITSUITE, 2026-11-17) A gate slot admits a GATE (and a hand-run suite); a
  shellcheck token admits one CHILD, inside or outside any gate. The two are different units and
  stay two pools; the token pool is the right granularity for a population that lives inside
  admitted gates.
- **Falsifier:** `tests/test_shellcheck_slots.py`
  `test_lint_runs_inside_a_gate_slot_are_still_bounded_per_child` (and its calibration with the
  bound off). In the estate: a memory incident whose children are shellchecks while the tokens
  were held to their bound, or a lint that waits on a token while its own gate holds the box idle.
- **Re-open when:** the gate slots become a weighted admission (a unit finer than a gate), a
  second child population (cargo, swiftlint) needs the same bound, or a measured child over
  8 GiB (`GIB_PER_TOKEN`) appears.
- **Lives in:** `crates/goh/src/shellcheck_slots.rs`, `crates/goh/src/shell_lint.rs`,
  `lib/lock_order.tsv`, `tests/test_shellcheck_slots.py`, `lib/admission.py`
