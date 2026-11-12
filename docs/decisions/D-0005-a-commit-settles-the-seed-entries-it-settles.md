# D-0005 — a commit's own run settles the store seed entries its change settles; the push's stale check stays as the backstop

- **Question:** An estate-seeded ratchet (`crates/goh/src/estate_seed.rs`: plural_hedge,
  mktemp_port, release_quiet, changelog_tags, hook_index) keeps an entry in the repository's tracked
  seed file or in the owner's private store. When a commit fixes the last seeded site of a file,
  who deletes the entry, and when?
- **Evidence:** On 2026-11-11/12 the commit passed pre-commit and the push or release went red on
  `stale GOH.PLURALHEDGE seed entry: delete its entry from ~/.local/state/goh/seeds/...` at least
  five times (koffee_big 2175d3d, servers, ZoneWM 2.111.0 twice, a loop agent; faults fd040e9c,
  ad1c1ea6). The store is in no tree, so nothing in the commit could delete the entry; each cost a
  failed ship and a coordinator's hand edit. A staged run (`full = false`) never judged staleness,
  and changelog_tags, which reads the whole file at both scopes, was red AT the commit on the same
  store entry with no way for the commit to clear it.
- **Options priced:**
  1. The push prunes every stale STORE entry itself instead of going red. Covers fixes that arrive
     with no commit-time run (an older goh, `--no-verify`, a fast-forward from another machine),
     but a finder that regresses to blind would erase the whole store on one push, and the only
     loud signal of a blind finder (every entry stale at once) would be gone. Rejected.
  2. A `goh seed-prune` command the push's red names. Still a hand step on every fix: the defect is
     that a human must act at all. Rejected.
  3. The commit's own run settles the entries of the paths it changes (deletions and both sides of
     a rename included; the whole changelog for changelog_tags): a STORE entry above what the staged
     tree holds is lowered, at 0 deleted, written by rename and said on the commit's output; a
     TRACKED entry left with nothing is red at the commit, which can delete it in the same commit.
     Growth is untouched (still red), a refused commit writes nothing (by any step: see the ordering below), and the push's stale check
     stays for the cases the commit never saw. TAKEN.
- **Ruling:** (GOH.SEEDSTALE, 2026-11-12) option 3: `estate_seed::settle`, `judge_settled`,
  `write_settled`, `settle_store`; every seeded check reaches it through `verdict`/`verdict_in`
  (hookindex through `judge_settled`). Shrinking a ratchet is always safe; only growth needs a
  reviewed entry. crates_behind's seed is retired (D-0001) and is not a subject.
  ORDERING (GOH.SEEDREFUSED, 2026-11-12): the settle is applied only after EVERY step of the
  commit's gate has passed, never when the seeded check alone passes. Under the commit hook
  (`gates/commit_gate.sh`, which sets `GOH_SEED_PENDING` to a directory of its own) a check
  journals its settle there (`estate_seed/pending.rs`); the hook runs `goh seed-settle <dir>`
  after its last step and its repo-writes check pass, and a refused commit's journal is deleted
  unapplied. A staged run outside the hook (no `GOH_SEED_PENDING`) still writes at once. The first
  cut wrote at the check: a necrohand agent's refused commits pruned 13 plural_hedge entries for
  files it then reverted, restored by hand.
- **Falsifier:** `tests/test_seed_settle.py` (a fix staged and committed leaves no stale store entry
  for the push across plural_hedge, mktemp_port, release_quiet and changelog_tags; a partial fix
  lowers; a deletion deletes; another repository's entry is untouched; growth and a refused commit
  write nothing; a tracked entry left stale is red at the commit; 8 of 12 red on v0.99.0's binary).
  `tests/test_seed_refused.py` (a commit the hook refuses at a step after the seeded check leaves
  the store byte-identical, red on v0.102.0; a passing one still settles).
  In the estate: a push red on a stale STORE entry whose fix was committed through the hook, or a
  store entry changed by a commit that was refused.
- **Re-open when:** a stale store entry stops a push again whose fix went through a commit-time run,
  or fixes arriving without one (another machine, `--no-verify`) become a regular source of red
  pushes (then price option 1 again behind a scanner-floor check).
- **Lives in:** `crates/goh/src/estate_seed.rs`, `crates/goh/src/estate_seed/pending.rs`,
  `gates/commit_gate.sh`, `crates/goh/src/hookindex/mod.rs`,
  `crates/goh/src/changelog_tags.rs`, `crates/goh/src/gitutil.rs` (`staged_paths`),
  `tests/test_seed_settle.py`, `tests/test_seed_refused.py`
