# One verdict store, one reuse API (GOH.ONEVERDICT)

goh reuses a verdict whenever the thing that decided it has not changed. It grew one store per
reuse path, each keyed its own way, and two paths that held the same fact did not see each other.
This note maps every store onto the one keyed store, `lib/reuse.py`, and says the order the rest
moves in. `tests/test_reuse_store.py` keeps the map honest: only `lib/reuse.py` names a store in
code, except the entries of its `TODAY` list, and each entry is deleted when its path moves.

## Why the ship missed the land's verdict (measured)

koffee_big ship `20261115T121828Z-952ef800` (`~/.local/state/goh/ship/koffee_big/`): the release
step took 3137.5 s, two full gates.

1. The handoff `952ef80` had reached origin through the land queue in `push` mode
   (`~/.local/state/goh/landq/done/*-CIP.KOFFEEBIG.json`, `"mode": "push"`). Its pre-push gate ran
   and went green, but the push gate wrote `green <sha>` only into the file `GOH_GATE_VERDICTS`
   names, and only `goh ship` set it: that verdict went nowhere.
2. Ship's own push step: `pre-push: nothing to gate (deletes, or commits the remote already has)`
   (`.push.log`). So the per-ship `.verdicts` file had no line for `952ef80`.
3. `tools/cut_release.sh` step 5 found no recorded verdict and ran `tools/gate.sh --full`
   (`.release.log` lines 95-867).
4. The kit committed the version bump `4950540` (`AboutView.swift`, `project.yml`) and pushed it:
   a new sha AND a new tree, gated in full again (`.release.log` 898-1668). The `.verdicts` file
   holds only `green 4950540`.

The VERDICTAPI skip was sha-keyed and per-ship. Step 3 is closed here: the push gate records every
green commit's TREE in the one store on every push, so the land queue's push is the ship's verdict.
Step 4 is a different tree, so no tree key reuses it: it is GOH.RELVERDICTREUSE's (the version
bump is release files only on a gated parent, the rule `lib/release_gate.py` already applies to
goh's own release), the store's next consumer. koffee_big's `tools/ship_verdict.py` still parses
the push log text instead of calling `goh ship verdict`; until it does, it misses either way.

## One goh change, verified up to eight times (GOH.ONEVERDICTTREE, measured 2026-11-19)

Where a goh change is judged on its way from a lane to the estate, what each stage reads, what
its verdict is keyed by, and what it costs. Sources: `~/.local/state/goh/landq/lander.log` and
`done/*.json` (the last 48 goh lands), the land gates' kept `logs/*.timings.jsonl`, and the ship
records `~/.local/state/goh/ship/gates_of_heck/*.release.timings.jsonl` and `*.sweep.json`.

| # | Stage | Reads | Key today | Measured |
| --- | --- | --- | --- | --- |
| 1 | lane's affected run (`tools/affected_tests.py --run`) | tests the diff selects | the `affected` path (repo, sha, selection, suite identity, suite inputs), since GOH.ONEVERDICTTREE3; it runs in the land's shape | as 2 |
| 2 | enqueue pre-step (`GOH_LAND_PRE_ONLY`) | the same selection, on the enqueued sha | takes 1's verdict on the exact key (lib/affected_verdict.py); kept per sha for the lander (`GOH_LAND_PRE_KEEP`), which reuses it where the tip moved only in paths no test reads | `pre_s` median 477 s before; the structural gates and cheap judges alone after a take |
| 3 | land gate (push gate in the land's export) | the whole gate; the Rust coverage step dominates | proven records per group; the coverage MEASUREMENT by `gate_identity.py rust` plus, until this item, every GOH_* in the environment | lock-to-landed median 848 s; coverage step median 504 s (13 kept timings, 375-867) |
| 4 | idle full suite (`lib/idle_suite.py`) | main's tip | the `full-suite` path (tree, instrumented); none owed where a land gate judged the whole tree | 11 runs in lander.log |
| 5 | release push (`tools/release_goh.sh`) | the push gate again, coverage included | as 3 | coverage median 457 s over 10 releases (380-980); 9 of the last 9 measured, "under another GOH_* configuration" |
| 6 | hosted CI (`.github/workflows/gate.yml`) | the pushed commit | none (independent runner) | not read by lanes |
| 7 | pin sweep | 12 consumer repos' pins | none | 713 s (v0.139.11) |
| 8 | reuse audit shadows (`lib/reuse_audit.py`) | one sampled reuse in `every` | re-runs the work | per path |

Land end to end (requested to landed) median 25.8 min over the 48 lands, 17 of them batched; the
SPD goal is 15 min. Rust coverage in lander.log: measured 26 times, reused 7. Of the 19 misses
named, 14 were "under another GOH_* configuration" and the rest "on other inputs", mostly goh's own
lands changing the coverage scripts (lib/reuse.py, gates/required_*.py); a batch prints its one
gate's decision once per item.

THE FIRST STEP, landed here: the measurement keys the GOH_* it can READ, by value (`goh-env` lines,
lib/cov_env.py), instead of the whole configuration, so stage 5 reuses stage 3's measurement when
the inputs match exactly and the miss names the variable when one moved. GOH.ONEVERDICTTREE3 made 1
and 2 one run (stage 2 takes stage 1's green verdict on an exact key) and the batch window adaptive
and bounded (lib/landq_window.py). What is left: stage 7 reusing the release's verdict; retiring
stage 8's shadows once the keys are proven; the batch cap chosen from the red rate; and the
coverage scripts' closure, which holds lib/reuse*.py (every reuse edit re-measures).

GOH.ONEVERDICTTREE4 (measured on the ship records, `~/.local/state/goh/ship/gates_of_heck/`):

* The ship's suite step and stage 4 ALREADY reuse stage 3 on an exact key wherever stage 3 judged
  the whole tree: v0.139.11, .12 and .14 skipped it, "already has its instrumented full run (by
  land gate)"; v0.139.13 ran it (779.2 s) because its land (GOH.CIWALL, 65 s) carried verdicts
  from another tree, which is no full run, so the owner's term 2 (a fresh full instrumented run
  before every ship) owes one. The key is the `carry` record's (repo, tree, suite tools) with
  `full` and `full_instrumented`; nothing else reaches a test (tests/suite_home.py drops every
  other GOH_* first, and every writer of the record runs in the land's `ci` shape, which a land
  key forces). What it lacked was the audit: the skip is the sampled `fullrun` path now.
* The release step (581.4 s in v0.139.14, 866.2 s in v0.139.13) is NOT the pin sweep, which runs
  detached after the ship (`*.sweep.json`, its own `created`): it is the release push's full gate,
  478.1 s and 731.0 s of `gates/rust_gate.sh` (the plain external suite 427.9 s), run because the
  audit turned `landed` off ("the audit turned the land verdict's reuse off", GOH.REUSEOFFLANDED4).
  `landed`'s fresh side is hosted CI, so a CI-only red turns a local reuse off.
* Stage 7 cannot reuse on (tree, goh content): a pin commit changes the consumer's tree and the
  goh it gates under (every goh release changes gates/, lib/ or tools/, all in the gate identity),
  so such a key never holds for a pin push. Its reuse is a rule like the release's (pin files only,
  on a parent gated green, under steps whose closure the release left alone), GOH.PINPUSHREUSE.

## The one store

`lib/reuse.py`. Root `GOH_REUSE_DIR`, else `$XDG_STATE_HOME/goh/reuse`, else
`~/.local/state/goh/reuse`; a record at `<root>/<path>/<key[:2]>/<key>.json`.

* A reuse PATH registers its name, its key fields and its TTL (`register`, one table in the
  module). The key is sha256 over the path and every registered field in order. A lookup names
  every field and nothing else, or raises: a partial key cannot be asked (the class CIP.CACHING
  calibrated twice: a proven step keyed on four tools of eight, a loaded verdict on the tree alone).
* A record carries its key, fields, verdict, writer and epoch. Absent, unreadable, under another
  key, or older than the TTL is a MISS, with the reason returned.
* THE AUDIT (GOH.REUSEAUDIT, `lib/reuse_audit.py`). Every reuse in goh is a registered path, its
  records here or still in their own store: `register` names its key fields, its SITES (the files
  that skip work on it), its store and its SHADOW. A site asks `use(path, fields)` first: "reuse",
  "shadow" (one use in `every`, counted, never by chance: do the work anyway, then `compare`) or
  "off". A disagreement re-runs both sides (the store read again, the work done again) and only a
  repeated one turns the path off: one recorded state, `<root>/audit/<path>.json`, with both
  verdicts, the key and the fault item it filed (`loopctl file`, as `class_of: none`; one loopctl
  refused is named at every SessionStart; an id the queue already holds is an earlier off's item,
  so the next free one, `GOH.REUSEOFF<PATH>2` and on, is filed naming it; a CI shadow's record
  names the run's `slug@pushed-sha` as `fresh_from`). It stays off until `reuse_audit.py
  enable PATH --fix SHA --test FILE::TEST` names the fix and the test that misses on the lacking
  input (the fix is read from the checkout: its own parent, else the one a from-head export was cut from, else the caller's cwd or GOH_DIR; a root that is no git repository is named, never "not a test"). A window (7 days) that compared fewer samples than min(min_samples, the samples it drew)
  is STARVED and turns the path off too; fewer drawn than min_samples reads "thin", never clean.
  goh's own shadow is hosted CI's full gate of every push (a "ci" shadow, queued, read once by the
  idle lander through `loopctl ci`; a run that judged nothing, cancelled or timed out, is no fresh
  side and is dropped uncompared, never red). A repo no hosted CI gates has nothing behind its skips: a
  path with `every_unbacked` counts such uses apart and samples them at that higher rate.
  `reuse_audit.py report [--json]` is the per-path count of uses, samples, disagreements and flakes.
  `tests/test_reuse_audit.py` refuses a reuse site no path names, an ask of an unregistered path
  and a path registered outside the one table.

The gate identity is `lib/gate_identity.py`'s key: the gate mode, the gates' content (goh's
version is its bytes, so no version string is keyed), the goh binary and every tool a step may run.
Its limit, by design: a goh land between a consumer's push and its ship is other gates, so the
ship misses and re-gates. A consumer whose hooks run a pinned goh keeps one identity until the pin
moves; one on the live checkout keeps it only while goh's main does not move.

## Every store, mapped

| Store | Keyed by today | Its path in the one store | State |
|---|---|---|---|
| `GOH_GATE_VERDICTS` per-ship file, `green <sha>` (push gate writes under `goh ship`; `goh ship verdict` reads) | sha, one ship | `gate` (repo, tree, identity of full/release/config) | MOVED: the push gate records every push; the query reads the store; the file and the variable are gone |
| `goh-landed`, `<sha> <identity>` (tools/land.sh writes; the release push rule and GOH_LANDED_ONLY read) | sha + identity | `gate`: a land is a full gate's green verdict on the tip's tree | READ through the API (`landed_identity`; `lookup_gate` counts a land line under today's identity). The writer moves when tools/land.sh's lane is free; the GOH_LANDED_ONLY refusal is provenance (did this sha come through land), not reuse, and stays sha-keyed |
| `goh-pushed`, `<sha> full\|release\|config` (push gate wrote; lib/config_push.py's floor rule read) | sha, how; NO identity | `pushed` (repo, tree): deliberately no identity, the floor rule certifies only what the remote gains under today's structural gate, so a key with the identity would turn it off at every goh change | MOVED: the same `record-gate` call writes it; config_push asks `reuse.pushed`; the file is no longer written, read only for a base pushed before the store |
| `goh-proven/<key>`, proven-step cache (gates/_proven.sh, `goh proven`, G2f) | tree, step, gate identity lines, keyed env | a `step` path (repo, tree, step, identity, env) | TO MOVE: two faces (shell, Rust); the record's artifact list rides as extra fields |
| Rust coverage record (gates/_rust_proven.sh) | rust identity lines | a `rustcov` path (repo, rust identity) | TO MOVE, with the proven cache |
| `~/.cache/goh/verdicts/<checker>/` (`GOH_VERDICT_DIR`, crates/goh/src/verdict_cache.rs) | per file: checker, bytes, path, tools | a `file` path (checker, key) | TO MOVE last: a Rust face of the API, hot per-file path (shell-lint) |
| `GOH_LAND_VERDICTS_RECORD/REUSE` (tests/_land_verdicts.py) | per test, one land's temp file | a `test` path (repo, tree, test id, suite identity) | TO MOVE: makes the pre-step's passes reusable beyond one land |
| the `carry` path (tests/_land_verdicts.py, GOH.VERDICTCARRY) | repo, tree, tools; the record holds each passed node | `carry`, in the one store | MOVED: every land's suite merges its passes; the gate carries a node across the diff affected_tests does not choose it for; a run that carried nothing is the FULL verdict a release needs (`reuse.py full-suite`, GOH_SHIP_FULL_SUITE) |
| `release_canary.memo_records` in the estate state file (scripts/release_canary.py, lib/canary_memo.py, GOH.CANARYMEMO) | per consumer and side: the side's gate content (docs, tests and *.md aside), binary bytes, tools, seed store, grace; the consumer's common dir and committed HEAD; one day | a `canary` path (side, repo, sha) | TO REGISTER: lib/reuse.py's table was held by another lane when it landed (GOH.CANARYMEMO2); unsampled until then |
| `goh-loaded/<tree>-<identity>.json` | tree + identity | none | GONE (GOH.LOADEDDROP removed the loaded run) |
| `lib/run_path` run dirs (`~/.local/state/goh/run`) | physical tree path | none | NOT A STORE: temp, lock and staging places; nothing reused from them is a verdict |

## The order

1. `gate`: the push gate's verdicts, `goh ship verdict`, the land record read (this item).
2. GOH.RELVERDICTREUSE: the release kit's version-bump commit, release files only on a parent the
   store holds green, asks `lookup_gate(parent)` (the koffee_big second gate).
3. `goh-pushed` onto `pushed` (done). Then tools/land.sh writes `record-gate`, and `goh-landed`
   keeps only the GOH_LANDED_ONLY provenance line.
4. The proven-step and Rust coverage records, then the per-test land record, then the per-file
   checker cache. GOH.VERDICTCARRY, GOH.CANARYMEMO, GOH.PROVENCLOSURE and GOH.PINPUSHREUSE register
   paths here instead of adding stores, each with its shadow.

## The audited paths (GOH.REUSEAUDIT)

| Path | Shadow | Site |
|---|---|---|
| `estate` | inline, 1 in 10: the entry judged afresh | `checks/_estate_cache.py` (`reusable`, `shadowed`) |
| `landed` | ci, every release push: hosted CI's full gate of the pushed commit | `lib/release_gate.py` (`audited`) |
| `carry` | inline, 1 in 10: the carried nodes of a source run anyway; a failure among them is re-run in a child pytest, and a repeat turns `carry` off | `tests/_land_verdicts.py` (`_carry`, `_shadows`) |
| `step` | inline, 1 in 16 hits, 1 in 4 where the repo tracks no GitHub workflow (no hosted CI behind the skip): the step runs anyway; a red one runs again and its record is looked up again, and a repeat turns `step` off, so every later hit runs (GOH.REUSEAUDIT2; the closure keying's kill switch, GOH.PROVENCLOSURE2) | `gates/_proven_audit.sh` (`proven_audit_use`, `proven_audit_judge`), asked by `gates/proven.sh` and `gates/local_ci.sh` before a skip. rust_gate's group hits (`rust_proven_group`) do not ask yet: they key on `closure full`, and their checks run under set -e |
| `test` | inline, 1 in 10 same-tree nodes a land's gate would skip: decided once by the controller and handed to every xdist worker, the node runs anyway; a failure is re-run in a child pytest and the pre-land record read again, and a repeat turns `test` off, so the next gate reuses nothing (GOH.REUSEAUDIT2) | `tests/_land_audit.py` (`audited`, `shadows`), asked by `tests/_land_verdicts.py` |
| `gate`, `origin`, `rustcov`, `file` | none: each registration says why (`reuse_audit.py report`, `goh budget`). `gate` waits on an idle-slot full gate only the idle lander can schedule; `origin` reuses no verdict (a scope rule); `rustcov` is minutes a sample under set -e; `file` needs the counter in Rust | |
| `affected` | inline, 1 in 10 takes: the set runs anyway and its verdict is compared with the kept one; a repeat turns `affected` off, so every set runs | `lib/affected_verdict.py` (`judged`), asked by `tools/affected_tests.py --run` |
| `fullrun` | inline, 1 in 10 skips of the ship's suite step: the instrumented full run is made anyway, fresh (no same-tree reuse), and compared with the record's green; a repeated red turns `fullrun` off, so every ship runs its suite | `lib/ship_full_run.py` (`decide`, `run`), asked by `tools/ship.py`'s `suite` step |
| `pushed` | none by design: identity-free | |
