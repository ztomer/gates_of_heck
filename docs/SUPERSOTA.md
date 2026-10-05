# Super SOTA: what that means here, and how we know

**Standing. Last revised 2026-10-02.**

This file states what "super SOTA" means for this estate in terms that can be
*checked*, records the evidence that produced each requirement, and keeps the
honest list of where we fall short. It is deliberately not a list of things we
like about ourselves.

Every requirement below was written because something went wrong. A requirement
nobody can fail is a wish, and a wish in a standards document is worse than
nothing, because it reads like a floor that is being met.

---

## 0. The scope, and the one fact that shapes everything

`gates_of_heck` is one checkout behind the pre-commit and pre-push of every
other repo (30 wired checkouts as of 2026-10-02). Hooks delegate here at runtime
via `GOH_DIR`, so:

* a fix **here** reaches 30 repos with zero re-installs;
* a fix **there** reaches one.

That asymmetry is why most of what is in this repo was not commissioned as
gates_of_heck work. The emptiness-assert check was written because four repos
were blocked by it (media_server 77 files, routines 50 assertions, app_updates
17, here 24). `dockerproxy_surface` and `log_rotation` were written for
media_server. `_rust_text.py` was extracted because a second checker needed it.

**So: everyone owns this repo, and finding a defect in it from any other repo is
a licence to fix it here, in that session.** The upgrade path is in §3.

---

## 1. The requirements

### R1 — A check is proven by RUNNING it, not by having tests

**Rule.** Every gate carries either an inline `--probe` (a self-proof that
exits non-zero on a planted violation) or a registry entry naming the file that
proves it. A test suite does not count: the tests and the checker share an
assumption, so they agree by construction.

**Why.** `checks/check_probes_pass.py` exists because 24 gates were all green
and none had ever been watched refusing anything. That day they were green by
luck, and luck is not a gate.

**Checked by.** `check_probes_pass.py`, which runs every declared self-proof, and — since
2026-10-03 — `checks/_calibration.py`, which reads `checks/gate_calibration.json` and holds every
entry in it to the estate. Both run on every gate run in every repo; a consumer repo with no
registry is reported, not failed.

### R2 — A checker's spec is a MEASURED TABLE, not a docstring

**Rule.** Where behaviour is version-dependent (a linter, a file format, an API),
the checker's cases are measured by running the tool, one shape per line, and
that table is the specification — in the docstring, in the `--probe`, and in the
tests.

**Why.** `check_no_empty_assert.py` carries a table of what clippy 1.99.0 does
and does not report, measured by running clippy. That is why its docstring can
be trusted, and why a reviewer can check the checker instead of the claim.

**Checked by.** `--probe` runs the table. Narrowing the table without a probe
going red is the defect.

### R3 — A gate that cannot fail in the shape it was written for is not proven

**Rule.** Every gate must be shown going red on a shape taken from the REAL
estate, not from a fixture invented beside it.

**Why — this is our single most repeated failure.** Two checks written in this
session were green across a dozen fixtures and wrong against reality:

| check | what it got wrong | found by |
|---|---|---|
| `check_no_empty_assert.py` | receiver pattern omitted `"`, so every emptiness assert with a string literal in its receiver was skipped | a real site in `storage-server` |
| `check_gluetun_socks5.rs` | read the LAST `image:` in the file, so it judged gluetun's pin by TRAWL's image | the live host, 40-odd services per compose file |

Both fixtures had exactly one service and one image — the shape the author had
imagined. **A fixture cannot disagree with the assumption that produced it.**

**Checked by.** Partly, since 2026-10-03, and the remainder is named rather
than implied.

`checks/check_estate_corpus.py` runs each declared house checker against a
**real subtree copied out of a real consumer repo** with one violation planted
inside it, and requires the checker to go red and name the planted file. Six
entries, each recording which corpus and why; the `check_no_empty_assert` entry
replays this requirement's own failure in the exact shape it failed in (a string
literal *inside* the receiver — the message form is the other silent shape and
would have tested something else). It runs from `check_probes_pass.py`, so it
runs on every gate run in every repo, at the cost of a corpus copy only on a
machine that has the estate.

Two rules in it exist because the first version of it was wrong, which is worth
recording because both would have made it report green while proving nothing:

* **the corpus must be CLEAN under the checker before the plant lands.** Replaying
  the 2026-10-02 receiver bug, the sweep reported 6/6 while the checker was blind
  to the plant: the corpus carried a finding of its own and the output named the
  file either way. A red that cannot be attributed to what caused it is not
  evidence.
* **a corpus below a file floor, and a plant that would land in a stub, are
  refusals.** A "real corpus" that quietly degraded to one file is the failure
  this gate exists to catch, reproduced inside the gate.

**What is still not checked here, stated plainly:**

* **whether the PLANT is the shape the estate really has.** The plant is written
  by the same person as the checker, and the first version of this gate proved
  nothing for exactly that reason — it planted `assert!(v.is_empty(), "msg")`
  (the shape clippy is silent on) and called the historical receiver bug
  covered. A plant cannot be derived from the estate mechanically without a
  language-aware mutator per checker, which is not built. Mitigation in place: one
  shape per entry, the reason recorded, and the plant re-derived from the real
  finding each time it was found not to discriminate.
* **whether a checker is CORRECT**, only that it still refuses. R3 claims the
  second; the first is not checkable without a live system.
* **the consumer checks.** Both examples in the table above are gates other repos
  own — `storage-server`'s is not even named in this file's history the way the
  house one is. The cross-repo survey that measures their coverage is
  `games/game_asset_factory/tools/check_gate_calibration.py`, and it remains
  there. **This is still the largest gap in this document**, narrowed to: a house
  checker is measured against real corpora, and a consumer checker is not
  measured at all.

### R4 — A gate that does not run must be visible

**Rule.** If a step is in the pipeline and the artefact serving the pipeline is
behind the source, that is a FAILURE, not a silent skip.

**Why.** On 2026-10-02 a stale `bin/goh` predated the markdown-links step, so
that step did not run — and a step that does not run prints exactly what a step
that passes prints. Two long-standing broken links were sitting in the tree the
whole time (one a TOC entry that contradicted the heading it linked to). The
same class had already been caught once by the parity suite: the shell tier ran
`check_python_formatted` in staged mode while the native tier skipped it.

**Checked by.** Currently: **partially.** `scripts/build-goh.sh:85-90` compares
the binary's version to `Cargo.toml`, but only *inside the build*. Nothing checks
it at gate time, so a lagging binary is invisible. `test_goh_structural_parity.py`
claims to compare step inventories and does not — it compares `(rc, failing
label)` tuples, so a step missing from one tier is invisible unless a fixture
happens to make it fail.

### R5 — A checker written for one repo is written HERE, and lands wired

**Rule.** No repo-local copies of house checkers. A checker that needs to exist
for one repo is added here and wired into that repo.

**Why.** `gates/rust_gate.sh` records it: until 2026-09-14 the step looked for a
repo-local `tools/check_no_allow.py` and skipped when absent, so four repos
carried vendored copies and the rest were not checked at all. The class
**recurred within a month** — `games/ZeroThunder` carried
`check_no_conflict_markers.py` and `check_no_screen_presentation.py`, both
sha-**diverged** from the house originals, one of them carrying a capability
(`DisplayPolicy`) the house copy does not have.

**Done for those two, 2026-10-03.** See R6 for what the capability diff found
and where it went. The house marker checker needed nothing — it is a strict
superset of the local copy's detection (see R6) — so that one was a straight
deletion.

**Checked by.** The deletions: nothing enforces the RULE. `AGENTS.md` states it;
no gate refuses a repo-local copy of a house checker. The natural home for that
gate is `gates/structural.sh` (one `goh_step`, reusing the file lists every other
structural checker already walks), which is outside the ownership this work had.
Stated as a gap rather than quietly left.

### R6 — Before replacing a repo-local checker with a house one, diff CAPABILITIES

**Rule.** A local copy may be doing something the house one does not. Diff what
they can do, not their bytes. Port the something before deleting the copy.

**Why.** Retiring monitor's own emoji checker for the house one silently lost
escaped-codepoint reading, because two padlocks had once hidden in its config
panel as Rust escapes. Found only when the house gate's own suite used an escape
as a fixture.

**Worked, 2026-10-03, on `games/ZeroThunder`'s two copies.** The capability diff
was read, not the bytes, and it said something neither `shasum` nor a line count
could:

| what the local copy did | house `check_no_conflict_markers.py` |
|---|---|
| `<<<<<<< ` + space, at line start | `^(<{7}\|>{7}\|{7})(\s\|$)` — space, tab, or end of line |
| staged mode read the **worktree** while staging | reads the **index** (`_gitutil.content_bytes`) |
| exit 3 = SKIP when not in a git repo | no such case; `repo_root()` is the caller's problem |

A strict superset, plus index truth the local copy did not have. **Nothing to
port.** The local copy is deleted, and the reason it could drift unnoticed is
also named: nothing ran both.

The screen checker was the opposite, and the interesting half:

* **`check_no_screen_presentation.py` was not a copy of the house
  `check_no_screen_presentation.py`.** Different rule, different scope. The house
  gate answers *test targets never touch the screen*; the local one answered *app
  source must route presentation through a seam*, and refused the seam's own
  file. Widening the house gate to cover it would have deleted a capability, not
  unified one — so the capability moved HERE as its own gate
  (`checks/check_display_seam.py`), the consumer's sweep roots / seam / exemptions
  / live-tier vocabulary became `tools/display_seam_policy.json`, and the copy was
  deleted. Same for `marker_reason.py`, which eight of ZeroThunder's gates
  imported: the rule moved to `checks/_marker_reason.py` and the consumer's file
  is now a re-export, because a rule about duplicates is the last thing to have
  two copies of.
* **What came across:** the seam exemption; the input tier (`NSEvent.mouseLocation`,
  `addGlobal|LocalMonitorForEvents`, `pressedMouseButtons`, `CGEvent.tapCreate`,
  `CGWarpMouseCursorPosition`, `CGDisplayMoveCursorToPoint`, `NSCursor.hide`,
  `.post(tap:)`) — input is the same problem as presentation, because they fight
  the user for the same mouse; `NSWorkspace.shared.open` of a system pane; the
  `input-ok:` marker beside `screen-ok:`; justified markers (a bare
  `// screen-ok:` silenced nothing locally either, and here it is the house rule
  in `checks/_marker_reason.py`); a live-tier declaration that must be a **call**
  and carry a substantive non-placeholder reason; the executor being allowed on
  an earlier line than the command, which is the shape `ruff format` emits and
  which the house copy is blind to; the global `.sh` pass; and an inline
  self-proof the house copy did not have at all.
* **What was measured and deliberately NOT ported:** the local copy's
  `APP_LAUNCH` regex also matched `pkill -f .*ZeroThunder`. `pkill` belongs to
  `check_no_kill_by_name.py`, which already owns it; putting a kill pattern
  inside a policy file the kill gate does not read would have been the bypass,
  not the port. No finding is lost — measured by running the house gate over the
  whole tree before and after.

### R7 — A registry nothing reads is a rumour

**Rule.** A calibration registry, a ratchet, or a baseline is either read by a
gate in this repo or it is deleted.

**Why.** `checks/gate_calibration.json` holds 23 entries claiming gates are
proven; `check_probes_pass.py` sweeps 11 gates and does not read the file. The
reader lives in another estate.

**Checked by.** `check_probes_pass.py`, via `checks/_calibration.py`. Four rules,
each of them a way the registry has lied or could:

* **the key names a gate that exists** — estate-independent, so it holds on a
  machine that has never heard of the other estates;
* **a cited prover resolves, and is COMMITTED at HEAD** — a file on disk is not a
  claim, it is a rumour its author alone can check;
* **a prover inside this repo ran the claimed self-proof green in the same
  sweep** — this is the half discovery structurally cannot see. The sweep runs
  what a gate *declares*, so a checker that stopped dispatching on `--probe`
  simply stops being run, silently, while the registry keeps earning it the word
  "proven". Measured: stripping the dispatch from `check_md_links.py` took the
  sweep from 11 self-proofs to 10 and left the gate GREEN; the registry reader
  fails that tree;
* **a prover outside this repo lists the key among what it proves** (`--proves`),
  and its estate is named in the output rather than assumed.

**Not checked here, and said so rather than implied.** The 18 canary citations
are verified for the two things this repo *can* see (committed; claimed) — the
red-on-violation run itself lives in `game_asset_factory`. That reader also
remains the cross-repo survey; this one deliberately is not a second copy of it,
because two numbers that can disagree are worse than one.

### R8 — A statement about the house belongs where the failure happens

**Rule.** A gate's failure output names the file that failed and says where it
lives. A docs file states the rule; the output routes to it.

**Why.** The native tier prints `(command: python3 check_md_links.py)` — a bare
filename with no path, from the tier that actually runs. The Python tier prints
the absolute path. A session that hits a surprising house gate has no way to find
the file from the message it was given.

---

## 2. What is genuinely strong here, stated without inflation

* **Self-proving gates with `--probe`.** Most estates have no gate discipline at
  all. 133/134 gates across the four-repo estate report proven.
* **Measured tables instead of doc-reading** (R2), and **differential
  calibration against real corpora** — the `fancy-regex` 0.14→0.19 bump was
  calibrated by parsing all 70 filenames on the backup volume with both engines
  and comparing against what the old engine had recorded.
* **Ratchets that go red in both directions.** The literals baseline fails when a
  count *drops*; the file-length ceiling fails when an exemption is dropped. Both
  caught real things this session.
* **A gate that reads a sibling repo's uncommitted tree names the tree.** The
  corpus gate prints `~/.claude/skills is outside this repo`, so a red step can
  be attributed before it is debugged.

---

## 3. Where we fall short, ranked

**Re-measured 2026-10-05, after v0.19.0.** Items 2, 4 and 5 are closed; item 3
is half; item 1 is narrowed. **No ranked item moved in v0.18.0 or v0.19.0**, and
saying so is the finding rather than leaving it implied. What v0.19.0 closed is
neither: it removed a cost and added a scope.

**The cost, and what it was not.** v0.18.0 grew `check_no_unreaped_spawn.py`'s
measured table 36 → 55 and the pytest suite went 226 s → 1190 s. Measured
contributor by contributor, the table was **0.06 s of it** — the probe is
in-process analysis over source strings, and 55 shapes cost a sixteenth of a
second. The minutes were two defects instead: the R3 estate sweep ran on every
`check_probes_pass.py` invocation including ones whose `--root` was a throwaway
fixture (53.7 s to answer a question about a one-file estate), and
`rust_findings` re-derived three crate-scope sets **once per file** over a
2.77 MB crate context, which is 23.2 s → 2.1 s over `media_server`'s 626-file
`crates/` tree. Suite: **1379.8 s → 735.6 s** of test time, **233.6 s → 105.7 s**
wall. The table was not shrunk.

**The scope.** R3's mechanism, and everything in this document, asks questions of
a **repo**. `.git/config` is not in a repo, which is why a live `gho_` token sat
in `remote.origin.url` while `check_no_secrets.py` printed `✓ OK` — proven in a
scratch repo, and now pinned by a test asserting that sibling's verdict. R3 does
not have a mechanism for a question about the *machine's* git configuration, and
`check_no_credential_urls.py` is that first entry rather than a closure of R3.
Its own limits are stated in its docstring: `.netrc`, `credential.helper` and
`http.*.extraheader` are all measured to carry tokens and none of them is
covered, and `docs/BACKLOG.md` carries them.

**And the lesson that outranks both.** The suite was slow because a gate asked
its question of the **wrong tree**, five times over, and the class went
unnoticed for a release because a 20-minute suite inside a 1800 s ceiling is
not red. A step nobody waits for is a step somebody reaches for
`--no-verify`. What remains is tracked in [`BACKLOG.md`](BACKLOG.md).

1. **R3 — NARROWED, not closed.** `checks/check_estate_corpus.py` plants a
   violation inside a real consumer corpus and requires the checker to go red
   and name the file. Its own first version proved nothing — it was 6/6 green
   while the 2026-10-02 receiver bug was replayed, because the plant was a shape
   clippy is silent on and the corpus carried a finding of its own. It now
   requires a clean corpus first. **Residual:** it cannot judge its own plant,
   it proves a checker still refuses rather than that it is correct, and it
   measures *house* checkers — the consumer class in R3's own table
   (`gluetun_socks5` reading the last `image:` of a 40-service compose file) is
   still unmeasured here.
2. **R4 — CLOSED.** A stale `bin/goh` was skipping steps silently; the version
   compare is now at gate time, and `test_both_tiers_run_the_same_steps`
   compares the tiers' step inventories **as sets** (the old `(rc, label)`
   comparison was blind to a missing step — 25 pre-existing cases passed with a
   step deleted). Residual: a version bump is visible, a step added without a
   bump is not; closing that needs a source hash embedded at build time.
3. **R5 — HALF.** ZeroThunder's two vendored copies are retired, one after a
   capability diff that found the local checker was **not** a copy of the house
   checker of that name — its rule was different, so widening the house gate
   would have deleted a capability. The capability landed as
   `checks/check_display_seam.py`. **Nothing refuses a future vendored copy.**
4. **R7 — CLOSED.** `checks/gate_calibration.json` is read, from
   `check_probes_pass.py`. Four rules, each a way the registry had been lying,
   including a prover that must have run green in the same sweep. It found a
   live key-spelling inconsistency on its first run — nothing had read the file.
5. **R8 — CLOSED.** The native tier prints the path the OS was handed and routes
   to the gate's documentation.

### The highest-severity item, unchanged

**Dirty `checks/` is now guarded, at both ends.** Publishing `bin/goh` refuses a
dirty tree, and every repo's pre-commit certifies by name. Both claims verified
against the code on 2026-10-04, with the two escape hatches stated rather than
implied: `scripts/build-goh.sh:70` is the refusal, over
`crates Cargo.toml Cargo.lock checks gates lib tui`, overridable only by
`GOH_BUILD_DIRTY=1`; `gates/structural.sh:88` is the naming half, over
`gates checks lib tui`, and it **warns** — `structural.sh` is layer 1, so every
repo's pre-commit runs it. The severity decision is worth recording: a gate-time
*refusal* was the wrong shape, because `structural.sh` and `push_gate.sh` are
spawned by every test that exercises them — a refusal is 22 tests red on any
session with uncommitted work here, and that is a gate whose verdict depends on
the working tree, one level up from the bug it was fixing.

## 4. Decisions taken 2026-10-02, and why

These are durable. The commit messages carry the evidence; this is the index.

| Decision | Why |
|---|---|
| **Read-idle HTTP policy moved into `app_updates`** (`core/src/idle.rs`) | ureq 3 has no read-idle timeout: `timeout_recv_body` is absolute from the first body byte, and `None` means a stalled socket is never interrupted at all. Owning it made the guarantee socket-free-testable and separated "how long a body may take" from "how long it may be silent". |
| **VPN egress: gluetun v3.41.3 (released) + a `3proxy` sidecar in its netns** | gluetun's SOCKS5 exists in **no released tag** — `settings/socks5.go` 404s at v3.41.3, the newest release. Pinning `:latest` was pinning an unreleased master build whose `version` string is literally `latest`. The sidecar (4.1 MB, arm64, first-party image, ATYP=0x03 and UDP ASSOCIATE verified in source) restores arbitrary-TCP egress from a **named version**, and keeps `app_updates`' host-side port unchanged. |
| **`:latest` for gluetun is NOT "the newest release"** | It is master HEAD. Verified: `:latest` == our pin == current master HEAD, and no released tag contains the feature we needed. On every other dependency here, "latest" means newest release; here the two answers are opposites. |
| **A stale `bin/goh` hid a gate step for a session** | Recorded because the failure mode generalises: a step's presence is a property of an artefact the gate does not verify. |
| **The agent-exceeded-scope provenance rule** | Work from an agent outside its brief arrived three times in `gates_of_heck` (a `plist` version source, a `pyproject` version source, and a stash conflict caused recovering from one). Each was read, verified and kept, with the provenance named in the commit message. Ownership means reading a foreign diff, not committing it unread. |

### Corrections made on 2026-10-02 (kept because the errors recur)

* **"Master is missing 54 production hotfixes"** — false, and it was repeated
  from a subagent before being checked. 51 of the 54 subjects are on master,
  re-landed independently; the 3 that are not are CI plumbing. The real cost of
  tracking master is no SLA and an unreproducible build, not missing patches.
* **A README link that resolved only on the machine that wrote it** —
  `./trawl-proxy` is an untracked stale build artifact. The check passed in the
  working tree and 404'd in a clean export.
* **`crates/core/src/http.rs` claimed "DNS goes through the tunnel too"** — false
  for the `socks5://` scheme in use, where ureq resolves locally
  (`ureq-3.4.2/src/proxy.rs:56-64`). `socks5h://` is the remote-resolve scheme.
  A confident, specific, wrong comment is a trap; it was trusted once already.