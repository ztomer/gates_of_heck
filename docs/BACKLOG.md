# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## Open

### From `check_no_unreaped_spawn.py` + `lib/orphan_canary.py` (2026-10-03)

Both landed green across 32 wired repos. These are the residuals, stated rather than left to be
discovered:

- **A child that calls `setsid()` is invisible to the canary.** It leaves the step's process group,
  and group membership is the only evidence the canary accepts (the `ppid == 1` alternative was
  measured cross-reporting every concurrent gate on the box). A daemonising test helper is a
  different design and would need its own signal — most likely the helper writing its own pidfile.
- **The checker is per-function, not interprocedural.** A spawn RETURNED to the caller is a handoff:
  counted, never a finding. Four across the estate. Following it means judging a callee's contract,
  which is the caller's judgement.
- **A kill inside a conditional is accepted.** `if cond { child.kill(); }` satisfies the rule
  statically and does nothing when `cond` is false. Measured cost: unknown; it needs a corpus.
- **Three languages.** Rust, Python, shell. Swift, JS/TS and Go are not read. The estate sweep found
  no spawn sites in any `.swift`/`.ts`/`.go` test file, so the cost is currently zero — re-measure
  rather than trust that.
- **Four real findings are reported, not fixed** — `routines/tests/follow_lifecycle.rs` (×3, a
  `sleep 300` with asserts between the spawn and the kill), `monitor`'s `ssh_tests.rs`,
  `games/ZeroThunder`'s `live_probe_lib.py`. Each repo's gate now refuses its own commits until they
  are fixed; they are in other checkouts and were left for their owners.

### Remaining from `docs/SUPERSOTA.md` §3

R4, R7 and R8 closed in v0.16.0. R3 **narrowed, not closed** — it cannot judge
its own plant, and it measures house checkers, not the consumer class in R3's
table. Carried here so the residual is not lost:

- **SUPERSOTA R3 — the residual.** `checks/check_estate_corpus.py` plants a
  violation inside a real consumer corpus and requires the checker to go red and
  name it. It proves a checker *still refuses*; it cannot say a checker is
  *correct*, and a plant derived from a real finding needs a per-checker
  language-aware mutator. Consumer-side checks are still unmeasured here.
- **SUPERSOTA R5 — the remaining half.** Nothing refuses a *future* vendored
  copy. Home is `gates/structural.sh`.
- **SUPERSOTA R4a — the honest limit.** The gate-time compare sees a version
  *bump*, not a step added. Closing it needs the binary to embed a hash of its
  source (`build.rs` under `crates/goh/`).
- **Uncommitted gate source is certified, not refused.** A push is certified by
  uncommitted gate source, with a warning. A refusal needs a seam the tests can
  set (an acknowledged-dirty marker file — a `GOH_*` key would need a
  `docs/config.md` row).
- **`check_probes_pass.py` went 3.5s -> 11.4s.** `check_estate_corpus.py` is a
  full-scope measurement sitting in a staged-scope path; move it behind
  `structural.sh --full`.
- **`tests/test_gate_environment.py` duplicates `_hermetic_env`.** The right
  home is `tests/conftest.py`.

- Secrets v2: entropy heuristics for unknown key shapes. Blocked on FP
  tuning first — v1 (prefixes + key headers) ships instead. Measure FP
  rate on all consumer trees before enforcing.
- shfmt enforcement: dropped from the shell-lint gate (tree uses aligned
  continuations shfmt won't reproduce). Revisit only as reformat-everything
  (noisy) or an explicit shfmt flag set proven clean on this tree.
- Swift coverage: parity-pinned, not merged. A merge needs a real-swift
  oracle proving identical verdicts across Xcode versions; without that
  toolchain, merging violates prove-before-claim. Revisit when it exists.
- Periodic disk watch: `scripts/bin/disk_hygiene.sh --warn-only` wants a
  launchd schedule (the prune-logs plist is the pattern). Manual runs only
  until then.
- `secret-ok` scope: line + line-above only. If fixture pressure appears,
  consider (not promise) path-scoped allow rules — never bare markers.
- Cross-run verdict cache (`GOH_CACHE`): designed, NOT built (Rust-port
  Phase 3d, 2026-09-23). After staged blobs were shared the native scanners
  cost tens of ms; what remains of a staged run is spawns inside delegated
  steps, which a verdict cache does not touch. If it is ever built, every way
  a hit can lie needs a test first: a config key the checker reads but the
  key omits; a blob hashed at another scope than policed (index truth is
  non-negotiable); a cached "clean" over ZERO files (the empty-scope refusal
  must survive a hit); a checker whose version changed. Re-open when a
  measured staged run is dominated by native scan time, not spawns.
- `lcov_merge` native port: killed by its entry gate (Phase 4b). The largest
  consumer (monitor, 6 parts) merges in 76 ms; the bar was a measured >1 s.
  Re-open when a consumer measures a merge over 1 s.
- `check_tests_registered.py` / `check_generated_fresh.py` stay Python: no
  shared gate runs them (Phase 3c scoped to what the gates actually invoke).

## Done (prune to history)

- Rust-port arc (2026-09-23, `docs/rust-port-plan.md` retired into git
  history): one shared tree walk and a single shellcheck call (staged shell
  lint ~640 ms faster); native home-paths, ceiling + ratchet, no-allow,
  screen presentation, lints opt-in and skills corpus, each parity-pinned and
  red-proven both ways; `goh-golden` bit-identical to the numpy tier
  (numpy's pairwise summation reproduced); staged blobs read once per run
  (197 git spawns → 15 on a 50-file commit).

- v0.8.0 arc: disk watch out of CI, pooled du, parallel suite, shell lint,
  rust coverage floor, secrets v1, swift parity, doctor, timeouts, timing,
  mcp split, xdist grouping, stub dedup, doc refresh.
