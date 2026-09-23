# Rust port plan — measured costs, gated phases, data-driven code rules

Status: proposal. If accepted, fold the phase list into `docs/BACKLOG.md`
and delete this file (rule #13). Planning only — no code changed.

Two requirements shape this plan. Every claim carries a measurement
(§2–§3), every phase has a data entry gate and a data exit gate (§4),
and every port must itself be data-driven code: no hardcoded
thresholds, no hardcoded strings, no duplicated vocabulary (§6).

## 1. Method

Machine: Apple silicon Mac, 2026-09-23. Subject: this repo —
217 tracked files, ~1.1 MB worktree bytes (`git ls-files | xargs cat |
wc -c` = 1,137,884). All timings are medians of 5 runs (3 for the slow
sweeps), wall clock via `perf_counter`, cold-ish (no warmup loop).
`python3 -c pass` startup: ~25 ms — the floor under every spawn.
Harness (throwaway, not committed):
`/var/folders/0f/w2bl2pnx4mn7pvrkjwvwt3p40000gn/T/opencode/time_steps.py`.

Caveats, stated upfront: failing steps still measure scan cost
(`no_home_paths` exits 1 on pre-existing tilde-path hits in docs — the
checker is opt-in and not wired here; `check_empty_scope.py` exits 1
naming `check_probes_pass.py` over the empty tree — pre-existing repo
state, untouched by this plan). `screen_presentation` was timed over
`tests/` including its intentional-violation fixtures, so its 60–68 ms
is scan cost, not a clean-tree pass. Numbers scale with tree size; §3
says which ones scale linearly and which are fixed.

## 2. Measured baseline (ms unless noted)

Already-ported scanners, native vs Python fallback:

| step | `goh` native | Python | ratio |
|---|---|---|---|
| emoji (full tree) | 19.8 | 563.3 | 28x |
| emoji (--staged) | 14.1 | 53.6 | 3.8x |
| conflict markers | 18.6 | 49.9 | 2.7x |
| file length | 17.4 | 58.4 | 3.4x |
| secrets | 24.5 | 144.6 | 5.9x |

Not yet ported (Python, full scope):

| step | ms | note |
|---|---|---|
| shell lint | ~1720–1890 | `bash -n` loop 107 ms + `shellcheck` 1383 ms over 42 sh files |
| `check_no_home_paths.py` | 125 | exits 1, see §1 |
| `check_no_allow.py` | 61 | clean pass |
| `check_no_screen_presentation.py` | 60–68 | over `tests/`, see §1 |
| `check_tests_registered.py` | 34 | `--buildsystem cmake --tests-dir tests` |
| `check_skills_corpus.py` | 46 | real corpus: 43 skills, 87 files |
| `check_empty_scope.py` | 1600 | full-only sweep over N gate subprocesses |
| `check_probes_pass.py` | 800 | full-only, 3 of 16 gates carry self-proofs |

Algorithmic hot spots (not gate latency, per-call cost):

| workload | slow tier | fast tier | ratio |
|---|---|---|---|
| golden 40 kpx (200x200) | pure-Python 23 ms | numpy 1.0 ms | 23x |
| golden 1 Mpx (1000x1000) | pure-Python 602 ms | numpy 27 ms | 22x |
| golden 6 Mpx (2000x3000) | pure-Python 3.56 s | numpy 155 ms | 23x |
| lcov merge 0.1 MB / 5 k lines | Python 7 ms | — | — |
| lcov merge 5.7 MB / 100 k lines, 6 parts | Python 230 ms | — | — |

## 3. What the numbers say

1. **The emoji win is banked and it was the big one: 543 ms saved per
   full run, 28x.** The per-char Python loop over every text file was
   the most expensive interpreted scan. Nothing remaining has that
   shape — the other three native scanners saved 30–120 ms each.
2. **Commit-path latency is shellcheck, not scanners.** Summed staged
   medians: native ≈ 1.85 s, fallback ≈ 2.59 s — and ~1.4 s of both is
   the `shellcheck` binary over 42 files. Porting every remaining
   commit-path scanner (`home_paths` 125 ms + corpus 46 ms + ceiling
   ~0 here) buys ~170 ms of 1.85 s. The largest commit-path lever is
   shell-lint scoping/parallelism, which is not a port at all.
3. **Push-path latency is the two sweeps: 2.4 s of spawn-dominated
   work.** `empty_scope` + `probes_pass` cost is N gate subprocesses;
   orchestrator language is noise. Confirms do-not-port (§5) with data.
4. **The golden fallback penalty is a flat 22–23x at every size.**
   A 6 Mpx frame costs 3.56 s without numpy, 155 ms with it. A Rust
   core matches the numpy column with no dependency — the prize is
   determinism + zero-dep speed, constant ratio, linear absolute.
5. **lcov merge is 230 ms at 5.7 MB.** A 10–20x Rust parse wins ~200 ms
   on this scale; the prize grows with workspace size (more targets →
   more parts), so this is a "measure on the largest consumer first"
   port, not a certain one.

## 4. Phases

A phase starts only when its entry gate is satisfied by data, and ends
only when its exit gate is measured — not when the code looks done.
Kill criterion per phase: if the exit gate is missed by >50%, stop the
whole plan and re-derive the model; later phases inherit the error.

### Phase 0 — Baseline. Status: done 2026-09-23.

The §2 table, committed in this file. Exit gate was: every port claim
in the draft plan backed by a median-of-5 number with method attached.
No phase below may cite an unmeasured cost.

### Phase 1 — Commit-path latency without ports

Goal: take the measured win the numbers point at (shell lint, 1.4 s)
before any new port.

- Entry gate: §2 row "shell lint ~1720–1890 ms" — already satisfied.
- Work: verify `--staged` shell lint is actually narrow (time it on a
  one-sh-file commit); run the 107 ms `bash -n` loop before shellcheck
  so syntax errors fail fast; parallelize per-file shellcheck runs;
  single shared tree enumeration for the four native scanners so later
  ports plug into one walk.
- Exit gate: staged total re-measured per §1 method, before/after
  attached to the commit. Pass bar: ≥500 ms off the staged median.
- Kill: if shell-lint work wins <500 ms, abandon latency work here —
  the remaining commit path is already fast enough and Phase 2
  proceeds on architectural (not latency) grounds.

### Phase 2 — Native layer 1

Goal: close the logic gap so layer 1 needs no interpreter except the
shellcheck binary.

- Entry gate: Phase 1 exit numbers recorded (whatever they are — this
  phase is justified architecturally, and the plan says so instead of
  inventing a latency prize: projected savings are only ~100 ms from
  `home_paths` 125 ms → ~20 ms native band, corpus 46 ms, ceiling ~0).
- Work: `check_no_home_paths.py` → native (same skeleton as
  `secrets.rs`); ceiling trio (`exclusion_has_ceiling`,
  `baseline_ratchet`, `loc_of_baseline_files`) → native.
  `check_shell_lint.sh` stays delegated (§5).
- Exit gate: `structural.sh` is `exec goh` + Python fallback +
  shellcheck delegation; `GOH_NO_NATIVE=1` still drives the full Python
  path; one new `test_goh_*_parity.py` per port, each proven red both
  directions.
- Kill: any port whose parity test cannot be proven red both ways is
  dropped from the phase, not shipped unproven.

### Phase 3 — Push-path scanners, one sub-phase each

Goal: delete interpreter startups from `--full`, sub-phase by
sub-phase so each can be killed independently.

- Entry gate: Phase 2 exit parity suites green in CI.
- Work, each its own commit with its own parity test:
  - 3a. `check_no_allow.py` (61 ms → projected ~15 ms).
  - 3b. `check_no_screen_presentation.py` (60–68 ms → ~20 ms).
  - 3c. Small-scanner batch: `tests_registered` (34 ms),
    `generated_fresh`, `lints_optin`, skills corpus (46 ms) —
    ~150 ms + 4 startups at once.
- Exit gate per sub-phase: parity red-both-ways demonstrated;
  measured saving ≥50% of projection or the sub-phase is reverted.
- Kill: 3b is the largest remaining checker (404 lines) — if its
  parity surface proves unmanageable, kill 3b alone; 3a/3c stand.

### Phase 3d — Architecture: caching and dedup evaluation

Goal: measure the redundant work the per-scanner pipeline still does
and price three remedies — shared blobs, shared enumeration with the
remaining delegated steps, cross-run verdict cache — adopting only
what the numbers and a staleness analysis support.

Seed measurement (2026-09-23, 7-file fixture, all staged, `git`
shimmed and counted): a staged run spawns git **36 times** — 28×
`git show` (4 native scanners × 7 staged blobs: one process per blob
per scanner), 5× shell-lint extraction, 2× enumeration, 2×
`rev-parse`. Staged cost scales as scanners × files in PROCESS
SPAWNS, not bytes; full-mode re-reads are cheap (~1.1 MB × scanners
here) but every delegated Python step re-enumerates and re-reads
independently, and the sweeps (`empty_scope`, `probes_pass`) make
every gate do it again per subprocess.

- Entry gate: 3a–3c parity suites green (the native surface whose
  sharing is evaluated must exist first).
- Work:
  1. Blob sharing: read each staged blob once per run and share it
     across native scanners (extends the Phase-1 enumeration sharing
     from names to content). Shared blobs also unlock the parallel
     scans Phase 1 deferred — fail-fast preserved by ordered
     reporting. Measure spawn-count + wall-clock delta on a 50-file
     staged fixture.
  2. Delegated-step handoff: pass the enumerated list (and, where it
     pays, blobs via one `git cat-file --batch` instead of N
     `git show`s) into the remaining Python steps (corpus, shell
     lint). Price it; adopt iff it halves delegated-step I/O.
  3. Cross-run verdict cache: design only. Key =
     (checker id, checker version, config hash, blob hash); miss =
     scan, hit = reuse. The staleness analysis comes FIRST and must
     enumerate every way a hit can lie: a config key the checker
     reads but the cache key omits; blob hashed at a different scope
     than policed (worktree vs index — index truth is non-negotiable);
     the empty-scope interaction (a cached "clean" over zero files
     must still refuse). Prototype behind `GOH_CACHE=1`, default off,
     with a cache-bypass parity test (cached verdicts == fresh
     verdicts across fixture mutations) before any default-on talk.
- Exit gate: (1)+(2) adopted iff measured ≥30% off the delegated-step
  total on the 50-file fixture; (3) lands as design + prototype only
  if every lie mode has a test — else it stays a design doc.
- Kill: if blob sharing wins <15% (spawns cheaper than modeled),
  kill 3d entirely — the architecture is already sharing enough.

### Phase 4 — Algorithmic ports, conditionally

Goal: per-call speedups outside gate latency. Both items are gated on
fresh measurements, not on §2 alone.

- Entry gate 4a (`goh-golden` lib): a consumer harness confirms it
  calibrates against metric numbers (not just verdicts) — else numeric
  parity has no customer. Exit gate: bit-identical metrics vs the
  numpy column at all three §2 sizes (40 kpx / 1 Mpx / 6 Mpx).
- Entry gate 4b (`lcov_merge`): the largest Rust consumer measures
  >1 s merges with the §1 method — else the 230 ms prize is not worth
  a port. Exit gate: ≥10x on that consumer's parts, shipped with the
  span-lookup fix (sorted spans + binary search) in the same change.
- Kill: if either entry gate is not met within one round of asking,
  the item leaves the plan permanently — no speculative ports.

### Phase 5 — Close-out

Goal: prove the plan, then remove it.

- Entry gate: all pursued phases exited with numbers attached.
- Work: re-measure the full §2 table end to end; attach before/after
  to the final commit; fold any surviving follow-ups into
  `docs/BACKLOG.md`; delete this file.
- Exit gate: the table shows every claimed win or documents the miss.

## 5. Do not port (with numbers)

- `check_empty_scope.py` (1.6 s) / `check_probes_pass.py` (0.8 s):
  subprocess-dominated; a Rust orchestrator saves ~50 ms of 2.4 s.
- `check_shell_lint.sh`: 107 ms wrapper + 1383 ms external binary.
  Nothing to compile.
- `eval_transport`, `mcp_scaffold`, `mcp_schema`: network/stdio-bound;
  compiled code makes no packet faster. stdlib-only is a feature.
- OS glue (`killtree`, `desktop_lock`, `tree_lock`, `headless_env`):
  tiny, correct, churn to port.
- `gates/*.sh` orchestration: process plumbing; bash is the tool.
- Legacy `check_swift_coverage.py`: parity-pinned, BACKLOG requires a
  real-Swift oracle before any merge. Untouched.

## 6. Data-driven code rules (binding on every phase above)

Core rule: no hardcoded strings or magic numbers in product code. Every
behavior-driving value — thresholds, limits, windows, counts, matcher
tokens, report caps — lives in a named constant, a table, or config;
prose templates stay with their format function (they are the message,
not the mechanism). Test fixtures and assertions are data by nature
and out of scope. Each rule names its enforcement — a rule without a
gate is a wish.

0. **Shared constants, single source.** Values used by more than one
   scanner live once: `gitutil::BINARY_SCAN_WINDOW` (8000),
   `gitutil::MAX_REPORT_HITS` (200). Module-local mirrors of a
   reference's own literal (`GENERATED_HEAD_LINES`, `MAX_REPORT_HITS`
   = 40 in `noallow`, the `g`-format digit counts in `ratchet`) are
   named consts with the reference documented. State machines are
   enums (`MaskState`), never integers. Conversions follow the
   `rust-no-suppression` skill (exact `num-traits` conversions,
   documented `try_from` fallbacks — never `as`). Enforcement: clippy
   pedantic + `cast_*` lints (already the gate), plus review against
   this section; pre-existing literals in `emoji`/`secrets`/`markers`
   migrated to the shared consts in Phase 2.

1. **No literal thresholds, timeouts, floors, or ceilings in Rust.**
   Current inventory that must become config-or-flags: `TIMEOUT = 90`
   (`empty_scope`), `TIMEOUT = 180` (`probes_pass`),
   `DEFAULT_MIN_SKILLS = 5`, `DEFAULT_MAX_WORDS = 5000`,
   `DEFAULT_TIMEOUT = 120.0`, `DEFAULT_PARSE_FLOOR = 0.6`,
   `DEFAULT_MIN_SAMPLES = 5`, `GOH_TAIL` default 60, golden
   `TOLERANCE_DEFAULTS`. Every number enters through `.gatesrc`
   (`GOH_*`), a CLI flag, or env — with the default documented in
   `docs/config.md` + `.gatesrc.example`. Enforcement: the existing
   `test_config_schema_covers_keys` meta-gate fails a new key without
   docs; add a magic-number assertion over `crates/goh/src` (numeric
   literals allowed only in `#[cfg(test)]` or a single `defaults.rs`
   table the config loader owns).
2. **No duplicated strings: one table, two emitters.** Step labels
   (`"no disallowed emoji"`, `"no conflict markers"`, …) are
   copy-pasted today between `steps.rs` and `structural.sh` — parity
   by diligence, drift by default. Each new port defines its labels
   once (a `STEPS` table in Rust, or a generated manifest both sides
   read) and the parity test asserts label-identical output, not just
   verdict-identical. Enforcement: parity tests compare full stdout,
   and a grep-test fails a label appearing in both trees unsourced.
3. **Vocabularies are tables, matching is table-driven.** Allowed
   glyphs (`ALLOWED_ORDERED`), suppression markers (`secret-ok:`,
   `path-ok:`, `screen-ok:`, `cov:ignore`), allow/expect tokens,
   screen-API patterns: each lives in exactly one ordered table with
   a comment per entry saying why it is there, and the scanner is a
   loop over the table. No inline regex alternation that only the
   author can extend. Enforcement: unit tests per table row (each
   pattern has a positive and a negative fixture — the existing
   `*-parity` and probe suites are the pattern).
4. **Decisions are data, not branches.** Ratchets, baselines,
   allowlists (`empty_scope_allow.json` shape: `legitimate` vs
   `known_blind` with reasons, stale-entry detection) — never
   `if repo == "x"` in code. A port that needs a per-repo exception
   ships the allowlist schema, not the exception.
5. **Parity tests are the executable spec.** Same bar as the existing
   `test_goh_*_parity.py`: identical verdicts step-for-step, proven
   red in both directions (break Rust once, break Python once),
   calibrated (fixture violation observed to fail) before green is
   trusted. For `goh-golden`: numeric parity against the numpy
   column across the three measured sizes (§2), since consumers
   calibrate against the numbers.
