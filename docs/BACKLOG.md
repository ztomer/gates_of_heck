# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## Open

### From `docs/SUPERSOTA.md` §3 — where we fall short, in the doc's order

Seeded 2026-10-02. The document states the ranked gap list; these are the boxes
that close it. Without these the list is a confession, not a plan.

- **SUPERSOTA R3, the part left over — nothing judges the PLANT, and consumer
  checks are unmeasured.** `checks/check_estate_corpus.py` (2026-10-03) runs six
  house checkers against real subtrees copied out of six real consumer repos and
  replays the 2026-10-02 receiver bug in the shape it failed in. Two gaps it
  states rather than hides: **(a)** the plant is authored beside the checker, so a
  checker that is blind to a shape the estate really has can still be measured
  green — the first version of the gate proved exactly that, and the honest fix
  is a language-aware mutator that derives each plant from a real finding in the
  corpus, per checker, which is not built; **(b)** it measures HOUSE checkers, and
  `gluetun_socks5`-class consumer checks stay where they were, covered only by
  the cross-repo survey in `games/game_asset_factory`. Also open: whether the
  corpus sweep belongs at `--staged` at all — it is a full-scope measurement
  (real trees, real corpora) and costs ~3 s of the ~11 s
  `check_probes_pass.py` now takes; moving it behind `structural.sh --full` is a
  `gates/` change.
- **SUPERSOTA R4 — a stale `bin/goh` silently skips steps.** Measured: a
  `bin/goh` predating the markdown-links step meant that step did not run,
  and a step that does not run prints what a passing step prints. Two
  long-standing broken links sat in the tree the whole time. Two parts:
  (a) ~6 lines at gate time comparing the binary's version to `Cargo.toml` —
  `scripts/build-goh.sh:85-90` does this but only *inside the build*;
  (b) a real step-inventory assertion in `test_goh_structural_parity.py`,
  which claims to compare inventories and does not — it compares
  `(rc, failing label)` tuples, so a step missing from one tier is invisible
  unless a fixture happens to make it fail.
- **SUPERSOTA R5, the part left over — nothing REFUSES a vendored copy.** The two
  that existed (`games/ZeroThunder`'s) were retired 2026-10-03 per **R6**, so the
  class is empty and nothing will notice the next one. A gate that refuses a
  repo-local copy of a house checker: its home is `gates/structural.sh`, one
  `goh_step` reusing the file list every structural checker already walks, and
  the house checker's own name is the thing to match on. Not started — outside
  the ownership of the round that retired the two.
- **SUPERSOTA R8 — the failing tier hides the path.** The native tier prints
  `(command: python3 check_md_links.py)` — a bare filename, from the tier
  that actually runs. The Python tier prints an absolute path. A session that
  hits a surprising house gate cannot find the file from the message it was
  given.
- **Dirty `checks/` is unguarded, and it is the highest-severity item in the
  document.** The Python tier consumes `checks/*.py` as **working-tree
  source**, so appending one comment line changes the gate every repo runs —
  no reinstall, no output, no refusal. `scripts/build-goh.sh` refuses to
  publish `bin/goh` from a dirty tree, but only for `crates/`. Silent and
  total.

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
