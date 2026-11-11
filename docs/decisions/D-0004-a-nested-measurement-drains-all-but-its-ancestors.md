# D-0004 — a measurement nested in a gate drains every gate but its ancestors; outside builds are load, not gates

- **Question:** (1) What does `bench_lock_exclusive` wait for when the measurement runs inside a
  gate? (2) Can a claim starve behind gates that keep arriving? (3) Should the lock also see
  builds started outside any goh gate (bare cargo, xcodebuild, a Makefile's clang)?
- **Evidence:** A downstream perf step (P5) runs inside its repo's ci_local inside a release. Its
  claim waited for every registered gate, its own ancestors among them, which cannot end before
  it does: a 180 s drain never once got the lock, and the caller fell back to measuring with no
  lock, reaching into the private `_bench_nested` to tell (faults 4fcda2ec, 5b44458b, 49cc734d,
  2026-11-09). Gates that arrive after a claim already wait behind it and the claim waits only
  for those queued before it (phase-fair, `tests/test_bench_lock.py`
  `test_quiet_takes_its_place_before_it_judges_the_box`, a stream of fake gates every 0.2 s).
- **Options priced:**
  1. A separate nested-claim entry point. Two claims that differ only in whether they deadlock;
     the non-nested caller has no registered ancestor, so skipping ancestors costs it nothing.
     Rejected.
  2. `bench_lock_exclusive` skips the caller's own ancestors (and itself) in the drain, always;
     `bench_lock_nested` is the public "am I inside a gate" question. TAKEN.
  3. For (3): enumerate compiler processes (`ps` for rustc, clang, swift-frontend) in the drain.
     Nothing makes such a process wait -- it never joins -- so the lock could only wait for the
     ones running and still be overtaken by the next; and a `ps` name match is the kill-by-name
     class the house forbids for judgement. Rejected.
  4. For (3): outside builds are LOAD. `tools/quiet.sh` claims, drains, then judges the box by its
     load average and lets go between attempts while it stays busy, bounded by its deadline; an
     agent's bare build beside a held load slot is refused by the Bash guard (`lib/load_bare.py`).
     TAKEN.
- **Ruling:** (GOH.BENCHNEST, 2026-11-11) `_bench_drained` skips `BENCH_LOCK_MINE` (this process
  and its ancestors, read once at the claim); `bench_lock_nested` is public. The drain cannot
  starve: no gate arriving after the claim joins it. Outside builds are seen as load by
  `tools/quiet.sh`, never as gates.
- **Falsifier:** `tests/test_bench_lock_nested.py` (a claim inside a joined gate gets the lock; a
  non-ancestor gate is still waited for; red on v0.97.0's lib). In the estate: a nested
  measurement that times out naming only its own ancestors, or a quiet.sh run that passes beside a
  bare build at full load.
- **Re-open when:** a measurement is spoiled by an outside build that `quiet.sh`'s load reading
  did not show, or a caller needs to measure with a gate ancestor's own load excluded.
- **Lives in:** `lib/bench_lock.sh`, `tests/test_bench_lock_nested.py`
