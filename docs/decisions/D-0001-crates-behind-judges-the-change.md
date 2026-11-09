# D-0001 — `goh crates-behind` is red only for a crate the change locked behind; drift warns

- **Question:** What may turn a repository's gate red when a `Cargo.lock` entry is behind its newest
  compatible release, given that the registry gains releases with no change in the repository?
- **Evidence:** `goh crates-behind` (v0.84 to v0.90.1) counted every entry `cargo update --dry-run`
  would move, against an estate seed of counts. On 2026-11-09 releases alone moved antiknob 2->3,
  app-mcp-bridge 11->12, monitor 30->31 (fuzz 16->17) and webcam_tune 8->9, ztools needed a
  same-day brotli-decompressor bump to push, and routines' push of 1c43bc4 and 77ba974 (neither
  touching its lock) was refused for 1 crate behind (`~/.cache/goh/push-logs/routines-77ba974-20261109T041507.log`).
  Instrument for the fix: the v0.90.1 binary on a fixture whose last commit edits only README, with
  a cargo reporting `itoa v1.0.18 -> v1.0.19`, exits 1; `tests/test_crates_drift.py` pins the new
  verdicts. Probe of index.crates.io on 2026-11-09: every recent version line carries `pubtime`
  (`itoa 1.0.19`: `2026-11-09T01:42:29Z`), older lines have none.
- **Options priced:**
  1. Judge only the lock entries the change touched (a `(name, version)` absent from the base's
     lock) -- needs a base: the push gate knows the remote's tip, a checkout its upstream, CI its
     parent; a depth-1 clone has none. Alone, a commit that locked the newest release of its day
     still goes red on a CI re-run after a later release. TAKEN, with 3.
  2. Compare against a registry snapshot recorded with the seed (behind as of the seed's date):
     the index's `pubtime` makes it possible, but the snapshot moves only when someone re-records
     it, so a crate a change adds behind a release newer than the snapshot passes, and every repo
     keeps a seed file to maintain. Rejected as the rule; its dating half is kept, with the
     commit's own time as the snapshot.
  3. Warn on live drift and gate only a newly added behind crate. TAKEN: "newly added" is 1, and
     "behind" is dated by the commit (a newer compatible release out before HEAD's committer time).
  4. Date by the commit with no base: every commit, a docs fix included, goes red on any drift
     released before it -- the same-day bump moves from "a release" to "every commit". Rejected.
  5. Keep the counted seed and re-seed on drift: the incident itself. Rejected.
- **Ruling:** (GOH.CRATESDRIFT, 2026-11-09) A behind entry is red only when the change being judged
  locked it -- its name and version are not in the base's lock, so an added crate and a downgrade
  are both the change's -- AND a newer, non-yanked release of its compatibility class was published
  before the change was committed (now, for an uncommitted lock); a git source has no dates and
  keeps the live verdict, as does a crate whose index cannot be read (said so). The base is
  `GOH_CHANGE_BASE` (the push gate sets it to the remote's tip: the whole pushed range), else the
  merge base with the upstream, else HEAD's parent, else (a root commit) the empty lock; no base
  (a shallow clone, an untracked lock) warns every entry and says why. Every other behind entry is
  DRIFT: one warning per lock naming each, every run. The counted seed `crates_behind_seed.json`
  is retired and named to delete wherever it stands. House Rust rule 2 ("latest dependencies")
  still bites in three places: every change that adds or moves a lock entry (red, at commit, push
  and CI); every gate run in every repo (the drift warning, surfaced by `goh_step`); and
  `goh crates-behind --live`, the owner's `cargo update` sweep, red on every behind entry.
- **Falsifier:** `tests/test_crates_drift.py` -- a docs-only change over a behind lock is green
  with the drift named; an added and a downgraded behind crate are red; a release published after
  the commit is not; the pushed range (`GOH_CHANGE_BASE`) is judged whole; `--live` is red on
  drift. In the estate: a consumer red on `crates-behind` for a commit whose range changed no
  `Cargo.lock` shows the ruling broken.
- **Re-open when:** any estate lock's warned drift stays above 30 entries for 30 days (the warning
  is not driving sweeps: schedule `--live`); crates.io drops `pubtime` from the sparse index; or
  cargo stabilises resolving as of a publish time, which could replace the index read.
- **Lives in:** `crates/goh/src/crates_change.rs`, `crates/goh/src/crates_change_tests.rs`,
  `crates/goh/src/crates_behind.rs`, `crates/goh/src/cli_args.rs`, `gates/push_gate.sh`, `lib/push_runs.sh`,
  `gates/rust_gate.sh`, `tests/test_crates_drift.py`
