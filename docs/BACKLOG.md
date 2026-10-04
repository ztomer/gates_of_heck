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
- **Sweep findings are reported, not fixed** — three repos were red and two are
  still. They are in other checkouts, their gates now refuse their own commits,
  and each repo's own roadmap/AGENTS carries its line. The index, with the state
  **re-measured rather than remembered**, is the section below.

### Estate findings from `checks/check_no_unreaped_spawn.py`

The incident that motivated the checker: `media_server`'s `archive_torznab` test
spawned a server that loops forever and reaped it *after* the assertion that can
panic. **Nine live processes** accumulated, each holding the cargo build lock, and
`cargo test` showed **no output at all** for 30 minutes while the suite itself
finished in 0.22s. The hang was the leak's symptom; the leak was invisible
because the test process was killed before it could report. It cost a day.
`media_server` is fixed (`cc70f6a`, shipped).

The checker landed and immediately found the same shape elsewhere. They are
recorded HERE rather than only in each repo's own backlog, because the class is
shared and the gate that found them is here: a repo whose gate is red refuses its
own commits, and **a red gate nobody has heard of is how a leak becomes an
outage**. Every row below was re-measured on 2026-10-04 by running the house
checker in that repo, read-only — the counts are the gate's, not this file's.

| repo | finding | state, measured 2026-10-04 |
|---|---|---|
| **routines** | `tests/follow_lifecycle.rs` — **3 sites** (lines 72, 95, 102): `Command::new("/bin/sleep").arg("300")` with `assert!` between the spawn and the `kill`/`wait` pair. **The same shape as the incident**, and the same cost: `sleep 300` is a five-minute orphan. | **UNFIXED — gate red (exit 1)** |
| **monitor** | `crates/multitop/src/ssh/ssh_tests.rs:88` — `child.stdin.take().unwrap().write_all(payload).unwrap()` above `child.wait()`. Two `unwrap()`s that can panic with the child live; the leaked process is the upload script, which starts an `sshd`. | **UNFIXED — gate red (exit 1)**; also separately red on clippy 1.99 — that one is monitor's own claim from its `docs/roadmap.md`, not re-measured here |
| **games/ZeroThunder** | `tests/e2e/live_probe_lib.py:31` — was `return proc if wait_for_app(...) else None`, **dropping the handle on the failure path**, so the failure branch was the leaking one | **FIXED** at `a6f6962`: `terminate` → `wait(timeout=5)` → `kill`, with the reason inline. Gate green. (This file previously also carried a "19 files dirty" warning from the R5 retirement; that tree is clean apart from `info.plist`, so the warning is withdrawn.) |
| **ztools** | 6 hits, all under `vendor/camoufox-rs` | **NOT findings** — green via `GOH_EXCLUDE='vendor/'`. Recorded so the 6 are not re-litigated; the exemption itself is measured below. |

So: **two unfixed leaks, in two repos, five sites.** The ztools row is the one
that could have quietly blinded the gate, so it was measured rather than believed
— planted in a throwaway copy of ztools' tracked tree, never in ztools itself:

| arm | what it shows |
|---|---|
| real tree, `--exclude 'vendor/'` as `.gatesrc` declares it | green — and **106 of ztools' own test files are still in scope**, so the gate is not switched off over ztools |
| the incident's ORDER planted in `rust/src/units_tests.rs`, exclusion still in force | **RED, exit 1**, naming the panicking line and the reap below it — a genuine spawn elsewhere in ztools is still caught |
| the same violation at `rust/src/myvendor/a_tests.rs` — a path that merely *contains* `vendor/` | **silently exempt, exit 0** |

**The residual, stated rather than left as a feeling:** `GOH_EXCLUDE` is compiled
with `re.search`, not `re.match` — it is a **substring** test, so `'vendor/'`
exempts any path with `vendor/` anywhere in it, not only a top-level vendored
tree. Today that is harmless: `vendor/camoufox-rs` is the only thing it matches in
ztools, and 106 first-party test files stay policed. The house checker's own usage
line says `--exclude '^vendor/'` (anchored, `checks/check_no_unreaped_spawn.py:7`)
and ztools declares the unanchored form. Re-anchor it when ztools' owner is next
in that repo; changing the semantics of a key every consumer's checker shares
belongs in its own release, not in this one. The anchoring is now written down in
`docs/config.md`.

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
