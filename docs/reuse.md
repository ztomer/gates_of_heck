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

## The one store

`lib/reuse.py`. Root `GOH_REUSE_DIR`, else `$XDG_STATE_HOME/goh/reuse`, else
`~/.local/state/goh/reuse`; a record at `<root>/<path>/<key[:2]>/<key>.json`.

* A reuse PATH registers its name, its key fields and its TTL (`register`, one table in the
  module). The key is sha256 over the path and every registered field in order. A lookup names
  every field and nothing else, or raises: a partial key cannot be asked (the class CIP.CACHING
  calibrated twice: a proven step keyed on four tools of eight, a loaded verdict on the tree alone).
* A record carries its key, fields, verdict, writer and epoch. Absent, unreadable, under another
  key, or older than the TTL is a MISS, with the reason returned.
* THE SHADOW CHECK. `shadow_due(key)` picks a deterministic share of keys (`GOH_REUSE_SHADOW`,
  0..1, off by default); a caller that re-ran the work hands the reused and the fresh verdict to
  `shadow_report`, which appends a line to `shadow.jsonl` in the store. A disagreement names a key
  missing an input (GOH.REUSEAUDIT reads it).

The gate identity is `lib/gate_identity.py`'s key: the gate mode, the gates' content (goh's
version is its bytes, so no version string is keyed), the goh binary and every tool a step may run.

## Every store, mapped

| Store | Keyed by today | Its path in the one store | State |
|---|---|---|---|
| `GOH_GATE_VERDICTS` per-ship file, `green <sha>` (push gate writes under `goh ship`; `goh ship verdict` reads) | sha, one ship | `gate` (repo, tree, identity of full/release/config) | MOVED: the push gate records every push; the query reads the store; the file and the variable are gone |
| `goh-landed`, `<sha> <identity>` (tools/land.sh writes; the release push rule and GOH_LANDED_ONLY read) | sha + identity | `gate`: a land is a full gate's green verdict on the tip's tree | READ through the API (`landed_identity`; `lookup_gate` counts a land line under today's identity). The writer moves when tools/land.sh's lane is free; the GOH_LANDED_ONLY refusal is provenance (did this sha come through land), not reuse, and stays sha-keyed |
| `goh-pushed`, `<sha> full\|release\|config` (push gate writes; lib/config_push.py's floor rule reads) | sha, how; NO identity | `gate`, the same record the push now writes (how is the identity's mode) | TO MOVE: config_push asks `lookup_gate(base, how)`; the file goes |
| `goh-proven/<key>`, proven-step cache (gates/_proven.sh, `goh proven`, G2f) | tree, step, gate identity lines, keyed env | a `step` path (repo, tree, step, identity, env) | TO MOVE: two faces (shell, Rust); the record's artifact list rides as extra fields |
| Rust coverage record (gates/_rust_proven.sh) | rust identity lines | a `rustcov` path (repo, rust identity) | TO MOVE, with the proven cache |
| `~/.cache/goh/verdicts/<checker>/` (`GOH_VERDICT_DIR`, crates/goh/src/verdict_cache.rs) | per file: checker, bytes, path, tools | a `file` path (checker, key) | TO MOVE last: a Rust face of the API, hot per-file path (shell-lint) |
| `GOH_LAND_VERDICTS_RECORD/REUSE` (tests/_land_verdicts.py) | per test, one land's temp file | a `test` path (repo, tree, test id, suite identity) | TO MOVE: makes the pre-step's passes reusable beyond one land |
| `goh-loaded/<tree>-<identity>.json` | tree + identity | none | GONE (GOH.LOADEDDROP removed the loaded run) |
| `lib/run_path` run dirs (`~/.local/state/goh/run`) | physical tree path | none | NOT A STORE: temp, lock and staging places; nothing reused from them is a verdict |

## The order

1. `gate`: the push gate's verdicts, `goh ship verdict`, the land record read (this item).
2. GOH.RELVERDICTREUSE: the release kit's version-bump commit, release files only on a parent the
   store holds green, asks `lookup_gate(parent)` (the koffee_big second gate).
3. `goh-pushed` onto `gate`; then tools/land.sh writes `record-gate` and `goh-landed` keeps only
   the GOH_LANDED_ONLY provenance line.
4. The proven-step and Rust coverage records, then the per-test land record, then the per-file
   checker cache. GOH.VERDICTCARRY, GOH.CANARYMEMO, GOH.PROVENCLOSURE and GOH.PINPUSHREUSE register
   paths here instead of adding stores; GOH.REUSEAUDIT turns on the shadow sample.
