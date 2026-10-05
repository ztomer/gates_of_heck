# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## Open

### From `check_no_unreaped_spawn.py` + `lib/orphan_canary.py` (2026-10-03)

Both landed green across 32 wired repos. These are the residuals, stated rather than left to be
discovered. **Re-measured 2026-10-05 after the v0.18.0 binding fix** — the sweep is the only place
this is true, and two of these changed:

- **A child that calls `setsid()` is invisible to the canary.** It leaves the step's process group,
  and group membership is the only evidence the canary accepts (the `ppid == 1` alternative was
  measured cross-reporting every concurrent gate on the box). A daemonising test helper is a
  different design and would need its own signal — most likely the helper writing its own pidfile.
  **UNCHANGED by v0.18.0**: detection is per-spawn, not per-group, so nothing in that release touched
  this.
- **The checker is per-function, not interprocedural.** A spawn RETURNED to the caller is a handoff:
  counted, never a finding. Four across the estate. Following it means judging a callee's contract,
  which is the caller's judgement. **NARROWED IN ONE RESPECT by v0.18.0, and only that:** a call to a
  function defined in the SAME FILE whose body can panic is now treated as panicking, because
  `routines`' `wait_for_socket` is a local function whose whole body is an `assert!` and it was the
  only construct between that spawn and its reap. A helper in another crate is still an unknown
  contract. A spawn crossing a file boundary remains a handoff.
- **Cross-CRATE guard types are not read.** A guard defined in `tests/common/` *is* (crate scope,
  measured over the estate: two files, both `routines`, and both of them sites the guard FIXES — a
  per-file reading reported correct code as broken). A guard that lives in a different **crate** is
  a dependency's contract and is reported `unjudgeable`: counted and named, never failed on.
- **A kill inside a conditional is accepted.** `if cond { child.kill(); }` satisfies the rule
  statically and does nothing when `cond` is false. Measured cost: unknown; it needs a corpus.
  **UNCHANGED by v0.18.0**, and worth saying plainly: the fix made the rules narrower in SCOPE, not
  stricter in CONTROL FLOW, so this hole is exactly as wide as it was.
- **Three languages.** Rust, Python, shell. Swift, JS/TS and Go are not read. The estate sweep found
  no spawn sites in any `.swift`/`.ts`/`.go` test file, so the cost is currently zero — re-measure
  rather than trust that.
- **A PEP 604 annotation under `checks/` needs `from __future__ import annotations`.** Found by
  running `./tools/gate.sh --full`, not by reading: the self-host install test died with
  `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` because `install.sh` runs on
  whatever interpreter the PATH offers. **A sweep of every module under `checks/`, `lib/`, `gates/`
  and `tests/` found 11 further instances** — one in `checks/` was mine and is fixed; the other ten
  are all in `tests/*.py`, which run under the session interpreter and so are not currently a
  failure. No gate forbids the omission: `pyproject.toml` deliberately enables the ruff FORMATTER
  only, with the comment "enabling a ruleset nobody has run would produce a red tree on day one",
  and enabling one is that repo's separate decision, not this release's.
- **Sweep findings are reported, not fixed** — the index below is **re-measured rather than
  remembered**. One repo is red on a spawn site; the other is an exclusion hazard, not a finding.
  They are in other checkouts, their gates now refuse their own commits, and each repo's own
  roadmap/AGENTS carries its line.

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
outage**.

**The table below was RE-MEASURED on 2026-10-05** by running this checker
(v0.18.0, post-fix) in every one of the 32 wired repos, read-only, honouring each
repo's own declared `GOH_EXCLUDE`. The counts are the gate's, not this file's.
The previous version of this table said "four real leaks" over rows that were not
all findings — the `ztools` row was an exemption and the `media_server` row was
already fixed — so the headline number is stated from the rows this time.

| repo | finding | state, measured 2026-10-05 |
|---|---|---|
| **routines** | `tests/follow_lifecycle.rs` — 3 sites: `Command::new("/bin/sleep").arg("300")` with `assert!` between the spawn and the `kill`/`wait` pair. The same shape as the incident, and the same cost: `sleep 300` is a five-minute orphan. | **FIXED** at `7e8cc4c`, shipped in **v0.59.1**. |
| **routines** | `tests/control_lifecycle.rs:227`, `tests/control_takeover.rs:190` — a RAW, unguarded owner beside a `Reap`-wrapped server, reaped below `wait_for_socket`'s `assert!`. A guard on `child` laundered `owner`; **the gate reported both clean.** | **FIXED** in the same `7e8cc4c`, shipped in **v0.59.1**, with all three sites sharing one root cause: the owner fixture and its guard now live once, in `tests/lifecycle_support/`. |
| **monitor** | `crates/multitop/src/ssh/ssh_tests.rs:88` — `child.stdin.take().unwrap().write_all(payload).unwrap()` above `child.wait()`. Two `unwrap()`s that can panic with the child live; the leaked process is the upload script, which starts an `sshd`. | **FIXED**, shipped in **v0.52.0**. |
| **monitor** | the same file's guard, `Reap::spawn`, **with its `Drop` body emptied to nothing**: the gate still reported clean. `Self(` was accepted whenever ANY `impl Drop` existed in the file, so a `Drop` that does nothing passed as protection — precisely the failure monitor's own `a_panic_after_the_spawn_leaves_no_child` test was written to catch, and the gate could not catch it. | **FIXED in this release** (`_self_type` resolves `Self`; `guard_types` still judges the body). |
| **games/ZeroThunder** | `tests/e2e/live_probe_lib.py:31` — was `return proc if wait_for_app(...) else None`, **dropping the handle on the failure path**, so the failure branch was the leaking one. | **FIXED** at `a6f6962`: `terminate` → `wait(timeout=5)` → `kill`, with the reason inline. (This file previously also carried a "19 files dirty" warning from the R5 retirement; that tree is clean apart from `info.plist`, so the warning is withdrawn.) |
| **ztools** | 6 hits, all under `vendor/camoufox-rs` | **NOT findings** — green via `GOH_EXCLUDE='vendor/'`. Recorded so the 6 are not re-litigated; the exemption itself is measured below. |

**So: one real leak remains unfixed in the estate, in one repo** — and the
count is 1 because it was re-measured, not because the number moved down:

| repo | finding | state, measured 2026-10-05 |
|---|---|---|
| **games/ZeroThunder** | `tests/e2e/garden_drag_flicker.py:73` — `proc = subprocess.Popen([binpath, ...])`, then `if not z.wait_for_app(timeout=15): return 2` at line 76, with `proc.terminate()` at line 119. **The identical dropped-handle defect** as the `live_probe_lib.py` row above, on the LIVE tier. | **UNFIXED, and the gate now says so** (exit 1). Deliberately not fixed: it needs a real screen, and a fix could not be *proven* here, so shipping one would violate prove-before-claim. **NEWLY VISIBLE** — see below. |
| **ztools** | the unanchored `GOH_EXCLUDE` hazard, not a spawn site: `'vendor/'` is a SUBSTRING test compiled with `re.search`, so it exempts any path with `vendor/` anywhere in it. | **UNFIXED**, and still not this gate's to change: re-anchoring `GOH_EXCLUDE` alters the semantics of a key every consumer's checker shares, so it belongs in its own release, in that repo. Harmless today — `vendor/camoufox-rs` is the only match and 106 first-party test files stay policed. |

**Why `garden_drag_flicker.py:73` became visible only now.** It is a Python
site, and Python's panic scan never counted a bare `return` as an exit that skips
a reap — Rust's `PANICS` always did. The asymmetry was not found by reading the
rule; it was found by reading a site the gate could not see. Three further shapes
it had been reporting wrongly were closed at the same time, all measured against
the real file: `return proc` is a HANDOFF and not a skip (that is `live_probe_lib.py`'s
own repaired form, and reading any `return` as a skip reported the repair as the
defect); a `return` under `proc.poll()` is a child already exited; and a `try:`
ABOVE the spawn with the reap in its `finally:` was not seen at all.

The ztools row is the one that could have quietly blinded the gate, so it was
measured rather than believed — planted in a throwaway copy of ztools' tracked
tree, never in ztools itself:

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

### Credential scope left open by `check_no_credential_urls.py` (2026-10-05)

The checker is scoped to git-config URLs and says so in its docstring. Four
adjacent shapes are deliberately NOT covered, each with a reason recorded there
rather than left for a reader to guess:

- **`.netrc` / `_netrc`** — a real credential store, out of scope because it is a
  file outside the repo and no house gate reads from `$HOME` anywhere else. Half
  a checker that reads `$HOME` is worse than none: it works on one machine, and
  the day it is wrong nobody can tell. Re-open when some gate already reads a
  path outside its repo, so there is a precedent to follow rather than invent.
- **`credential.helper`** — measured: multi-valued, and any value may carry a
  token (`!f() { echo username=…; }; f`). It is not a URL, so the rule does not
  reach it. The rule that would: a credential-NAMED key holding an opaque value,
  which is `check_no_secrets.py`'s existing pattern applied to `.git/config`
  instead of to tracked files.
- **`http.*.extraheader`** — measured to carry `AUTHORIZATION: basic <token>`.
  Same shape, same fix, same reason it is separate: it is a header, not a URL.
- **CI configs** — a token in `.github/workflows` IS committed, so
  `check_no_secrets.py` already has it. Duplicating its patterns in a second
  checker would be the second copy a rule about duplicates should never have.
  NOT a gap; listed so nobody re-raises it as one.

The two `.git/config` shapes are the real work and they want ONE change, not
two: read `.git/config` with the credential-named-key rule `check_no_secrets.py`
already owns, so a token in `credential.helper` or `http.*.extraheader` is caught
by the rule that exists rather than by a third variant of it.

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
