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

**Checked by.** `check_probes_pass.py`, which runs every declared self-proof.
`checks/gate_calibration.json` is the registry — **currently read by nobody in
this repo**, which is a known gap (R7).

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

**Checked by.** Currently: not enforced. The mechanism exists elsewhere —
`games/game_asset_factory/tools/check_gate_calibration.py` proves gates against
the real estate and reports `gates_of_heck 24/25`. Ours does not. **This is the
largest single gap in this document.**

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
**recurred within a month** — `games/ZeroThunder` currently carries
`check_no_conflict_markers.py` and `check_no_screen_presentation.py`, both
sha-**diverged** from the house originals, one of them carrying a capability
(`DisplayPolicy`) the house copy does not have.

**Checked by.** Currently: nothing. `AGENTS.md` states the rule; no gate
enforces it.

### R6 — Before replacing a repo-local checker with a house one, diff CAPABILITIES

**Rule.** A local copy may be doing something the house one does not. Diff what
they can do, not their bytes. Port the something before deleting the copy.

**Why.** Retiring monitor's own emoji checker for the house one silently lost
escaped-codepoint reading, because two padlocks had once hidden in its config
panel as Rust escapes. Found only when the house gate's own suite used an escape
as a fixture.

### R7 — A registry nothing reads is a rumour

**Rule.** A calibration registry, a ratchet, or a baseline is either read by a
gate in this repo or it is deleted.

**Why.** `checks/gate_calibration.json` holds 23 entries claiming gates are
proven; `check_probes_pass.py` sweeps 11 gates and does not read the file. The
reader lives in another estate.

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

1. **R3 — checks are proven against fixtures we wrote.** Two of this session's
   own checks were wrong against reality. Highest value fix: run each new or
   changed house checker against the other repos before it lands.
2. **R4 — a stale binary silently skips steps.** ~6 lines at gate time would
   close the measured instance; a step-inventory assertion in the parity suite
   would close the class.
3. **R5 — vendored checker copies are unenforced.** Two exist right now.
4. **R7 — the calibration registry is read by nobody here.**
5. **R8 — the failing tier hides the path.**

Items 2–4 are small. Item 1 is the one that changes outcomes.

### A risk of shared ownership nobody has guarded

The Python tier consumes `checks/*.py` as **working-tree source**. Appending one
comment line changes the gate every repo runs — no reinstall, no output, no
refusal. `scripts/build-goh.sh` refuses to publish `bin/goh` from a dirty tree,
but only for `crates/`. A dirty `checks/` is unguarded, and that is the highest
severity item in this document: it is silent and total.

---

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