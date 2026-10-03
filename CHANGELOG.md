# CHANGELOG

## Unreleased — the display-seam gate, and ZeroThunder's two vendored checkers retired _(2026-10-03)_

SUPERSOTA **R5/R6**. `games/ZeroThunder` carried repo-local copies of two house
checkers, both sha-diverged. The rule is that a checker one repo needs is added
here and wired into that repo; the caution is that a local copy may be doing
something the house one does not, so diff **capabilities**, not bytes.

**`check_no_conflict_markers.py`** needed nothing. The house copy is a strict
superset of the local one's detection (`\s` or end-of-line after the marker, not
just a space) and additionally reads THE INDEX in `--staged` where the local copy
read the worktree. Deleted; ZeroThunder now runs the house one, which
`gates/structural.sh` was already running for every repo anyway — the local copy
had been invisible precisely because it duplicated a gate that already ran.

**`check_no_screen_presentation.py`** was not a copy of the house checker of that
name. Different rule, different scope: the house gate refuses screen APIs in TEST
TARGETS; this one refused them in APP SOURCE unless they routed through a seam,
and exempted the seam's own file. Widening the house gate would have deleted a
capability, so the capability moved here as its own gate.

`checks/check_display_seam.py` — on-screen presentation and real-input use route
through a seam the consumer names; a live-tier harness must CALL the helper and
carry a substantive, non-placeholder reason; every tracked `*.sh` is policed
whatever the sweep roots say. The consumer's sweep roots, seam, exemption list
and live-tier vocabulary are policy: `tools/display_seam_policy.json`, committed
in the consumer and read by the house gate. `docs/new-checker.md` §3 forbids
per-repo policy inside shared code; a policy file is the other half of that.

Ported across, none of which the house checker had: the input tier
(`NSEvent.mouseLocation`, event taps, `pressedMouseButtons`, cursor warp/hide,
`.post(tap:)`), `NSWorkspace.shared.open` of a system pane, the `input-ok:`
marker, **justified** markers (a bare `// screen-ok:` silences nothing — that
rule moved to `checks/_marker_reason.py`, and ZeroThunder's `tools/marker_reason.py`,
imported by eight of its gates, is now a re-export of it rather than a fourth
copy), a live declaration that must be a call rather than a mention, the executor
allowed on an **earlier line** than the command (the shape `ruff format` emits,
and which the house checker is blind to), the global shell pass, and an inline
self-proof.

Measured and deliberately NOT ported: the local `APP_LAUNCH` regex also matched
`pkill -f .*ZeroThunder`. `pkill` belongs to `check_no_kill_by_name.py`, which
already owns it; a kill pattern inside a policy file that gate does not read
would be the bypass, not the port. No finding is lost — the house gate was run
over the whole tree before and after.

**`--probe` is 58 cases, and every rule is calibrated.** One case per pattern in
the Swift and live-command vocabularies (a pattern nobody drives is a pattern
nobody knows works), plus the config refusals. `tests/test_display_seam.py`
re-runs the probe against a copy of the real module with each of its 23 rules
neutered, so dropping one turns its own row red and nothing else:

    swift-input-family    -> Swift: reading the real pointer is refused
    swift-tap-family      -> Swift: CGEvent.tapCreate is refused
    masking               -> Swift: a string naming a shape is prose, not code
    justified-marker      -> a BARE screen-ok marker silences nothing
    lookback              -> a MULTI-LINE subprocess call is caught
    helper-must-be-called -> merely DOCUMENTING the helper is not a declaration
    live-reason           -> a PLACEHOLDER live reason is a violation
    shell-pass-global     -> an unguarded .sh is a violation, wherever it lives
    offscreen-declared    -> a SECOND offscreen mode inherits no recognition
    unknown-key-refused   -> an unknown policy key is refused, not ignored
    zero-scope-refused    -> a policy matching no source is REFUSED, not passed

ZeroThunder's coverage afterwards, planted and caught, not assumed: a planted
`makeKeyAndOrderFront` in `UI/` (red, and red again through `--staged`); an
undeclared `screencapture` harness in `tests/`; an unguarded `osascript` at the
repo root; a **bare** `// screen-ok:` — the hole the original gate was measured
against; and a planted conflict marker caught by the house marker checker.

## Unreleased — the calibration registry is read here, and every claim in it is checked _(2026-10-03)_

SUPERSOTA **R7**. `checks/gate_calibration.json` had held 23 entries claiming
named gates had proven they can fail, and nothing in this repo read it:
`check_probes_pass.py` sweeps what a gate *declares*, and the registry was not
part of what a gate declares. The only reader lived in another estate
(`games/game_asset_factory/tools/check_gate_calibration.py`, which is also the
cross-repo survey and remains so).

The defect is sharper than "a file nobody reads". The sweep runs what
`discover()` finds, so a checker that stopped dispatching on `--probe` simply
stops being run — silently, with no output, while the registry goes on calling
it proven. Measured on this tree: stripping the `--probe` dispatch from
`check_md_links.py` took the sweep from 11 self-proofs to 10 and left the gate
**green**.

`checks/_calibration.py` reads the registry and holds every entry to the estate:

* the key must name a gate that exists (estate-independent — it holds on a
  machine that has never heard of the other estates);
* a cited prover must resolve, and be **committed at HEAD**: a file on disk is
  not a claim, it is a rumour its author alone can check;
* a prover inside this repo must have had the claimed self-proof run green by
  the same sweep;
* a prover outside this repo must list the key among what it proves
  (`--proves`), and its estate is named in the output rather than assumed.

It runs from `check_probes_pass.py`, so it is read on every gate run in every
repo; a consumer repo with no registry is reported, not failed. It is not a
second copy of the external survey — two numbers that can disagree are worse
than one.

It also found a live inconsistency on its first run: `known_unproven` spelled
its key `check_swift_coverage` where `proven_by` spells the same gate
`swift_coverage`. Nothing had read the file.

Self-proof: `checks/_calibration_probe.py`, split out so neither file crowds
the 500-line cap, driven by `check_probes_pass.py --probe`. Fourteen cases —
every way the registry can lie asserted red, the sound registry asserted clean —
and `tests/test_gate_calibration.py` re-runs the probe with each rule
neutered, so the reader cannot lose a check quietly.

## v0.15.1 — two version layouts a release could not be checked against _(2026-10-02)_

`v0.15.0` shipped with the emptiness-assert gate and the declared Python rule
set. These two are the same class of defect found afterwards, and both of them
arrived from an agent working nearby rather than being commissioned — read
before keeping, and named here so the record is honest about it.

**`plist`** — an Apple bundle declares its version as a `<string>` under a
`<key>` in `Info.plist`, which was none of the layouts this gate shipped with.
Measured on ZeroThunder, whose `v2.10.0` tag no strategy could check:
`file:` reads the XML **declaration** as the version
(`('(file)', '<?xml version="1.0"…?>')`) because it takes the first line it does
not read as a `#` comment, compares the tag against "1.0", and reports a correct
release as a mismatch; `swift:` and `xcconfig:` correctly find nothing, because
there is no `let` and no `SETTING =`. So a repo that declares its version the way
Apple ships it was UNVERIFIABLE, while this gate's own rule is that an absent
version source is a finding rather than a pass.

**`pyproject`** — the `[project] version` a Python distribution declares. Same
shape of blindness: without a strategy for it, a packaging-only repo has no
readable source at all and `file:` reads whatever the first meaningful line
happens to be.

One entry in `STRATEGIES` each, no new code path. What I added to both:
`docs/config.md` enumerates the kinds, and a kind nothing documents is
half-wired.

## v0.15.0 — the emptiness assert clippy cannot see, and a declared rule set for Python _(2026-10-02)_

`check_no_empty_assert.py`, wired into every Rust repo's gate ahead of clippy.

The class it closes was not hypothetical and not one repository. Four repos in
two days: media_server 77 files, routines 50 assertions, app_updates 17,
gates_of_heck 24 — and every single one of them was a release blocker rather
than a broken build. media_server's `bump.sh` refused to cut for exactly this
reason, so a stylistic lint held a release.

What makes this a gate and not another sweep is that **clippy is blind to half
the shape**. Measured against clippy 1.99.0, one shape per line:

    assert!(v.is_empty());                        assert_is_empty
    assert!(!v.is_empty());                       assert_is_empty
    assert!(v.len() == 0);   /  assert!(0 == v.len());   len_zero
    assert!(v.len() > 0);                         len_zero
    debug_assert!(v.is_empty());                  assert_is_empty
    assert!(v.is_empty(), "with a message");      NOTHING
    assert!(!v.is_empty(), "msg");                NOTHING
    assert_eq!(v.len(), 0);                       nothing (clippy's own suggestion)

The two silent rows are why the class kept recurring: an assert carrying a
message is the form people write on purpose, and it passes. That is not a
theory here — clippy was green on this repository while **5 instances sat in the
tree**, two commits after a sweep for the very same lint. Those five are fixed in
this change, which is the first evidence the gap was real and the second that
the sweep was incomplete.

The rows that stay clean matter as much: `assert_eq!(x.len(), 0)` is not a
violation, it is what clippy asks you to write, and a checker that flagged it
would be flagging the fix. Both directions are cases in `--probe`, which is
registered in `gate_calibration.json`, so a future narrowing of the match list
turns a gate green again by going blind — and the calibration test breaks the
pattern on purpose to prove the probe notices.

Also in this change, both from the same session's findings rather than from
preference:

* `checks/_rust_text.py` — the comment-stripping depth counter, the string-literal
  regex, the compiled-source scope rule and the `@generated` marker, shared by
  `check_no_allow.py` and the new checker. Two checkers needing the same three
  things is how a third copy appears; the second one to need it did not write one.
* `qbittorrent.rs` and `screen.rs` move their tests to submodules. Both sat at
  the 500-line cap with tests inline, and twice in one session a test edit pushed
  a file over it. The cost of that class is paid by the next change, at commit
  time, in the gate.


## v0.14.0 — four gate gaps an independent audit found, closed structurally _(2026-10-01)_

This stanza did not exist when the tag was cut. The release bumped the workspace
to 0.14.0 and shipped without one, so the changelog's newest entry was v0.13.5 —
a released version with no record of what it contained. Written from the tagged
commit rather than from memory, and deliberately short: the four fixes are
described where they live.

* A Python tree with no formatter has its shape decided by whichever hand last
  edited it. `check_python_formatted.py` now runs ruff over a tree that declares
  no configuration, and says which rule set it applied instead of letting the
  default decide silently.
* The lockfile said 1.35.0 while the manifest said 1.36.0. `check_lock_version.py`
  reads both and fails on the disagreement, because that is the shape of a
  release cut from a stale lock.
* `check_tag_version.py` refuses to push a `v<semver>` tag whose target commit
  declares a different version — the defect that made a published tag able to
  name a commit whose own build reported itself as another version.
* A gate that scanned nothing read exactly like a gate that found nothing.
  `check_probes_pass.py` runs every gate's own self-proof, after 24 of them were
  found to be green by luck rather than by working.

## v0.13.5 — a tag is a claim, and it is checked against its own commit _(2026-10-01)_

`check_tag_version.py` refuses to push any `refs/tags/v<semver>` whose target
commit declares a different version. It exists because of media_server, today:
a version was bumped, `git commit --amend --no-edit` was REJECTED by pre-commit
twice, and `2>/dev/null` swallowed both refusals so the amend looked like it
had worked. `git tag -f v1.79.3` then named commit `a266067`, whose VERSION file
and all 31 crate manifests still said 1.79.1, and `git push --follow-tags`
published it. Anyone checking out `v1.79.3` got a build that reported itself as
1.79.1 — a name that cannot tell two different binaries apart, except the name
outlives the mistake. The repo's own `test_native_version_flag` DID catch the
mismatch; it ran in the pre-push gate, but nothing tied it to the TAG.

Three properties, each a place the obvious implementation is wrong:

- **The refs being PUSHED**, from the hook's stdin, not every tag in the repo.
  Scanning all tags lets one stale tag veto every unrelated push until somebody
  deletes it — a gate that cries wolf gets `--no-verify`'d.
- **The version at the COMMIT** (`git show <commit>:<path>`, `^{commit}` to peel
  an annotated tag), never the working tree. The tree is not the thing being
  published; in the incident it held the fix the amend was about to lose.
- **An absent version source is a FINDING.** `v2.0.0` on a tree that declares no
  version is unverifiable, and unverifiable read as fine is how the next one
  ships.

Version sources are a table, not a repo. Both live layouts ship by default — a
`VERSION` file (media_server) and `[workspace.package] version` (app_updates) —
and `GOH_TAG_VERSION_SOURCES` in `.gatesrc` points it anywhere else, with
opt-in globs expanded against the tree at the commit. Globbing is scoped on
purpose: a bare `**/Cargo.toml` sweeps `vendor/`, and app_updates' camoufox-rs
declares 0.1.0, which is not that repo's release number. A glob matching nothing
is reported, so a typo cannot retire a strategy in silence.

It runs in `gates/push_gate.sh` FIRST, over the refs as given, because both
skips below it are about CODE: a commit the remote already holds, or one already
gated in this push, would otherwise let a lying tag through unexamined — and
re-tagging an old commit to a new version is exactly how a lie gets published.
`push_gate.sh` also now captures stdin to a file first, since a stream read
twice is a stream read once.

Calibrated red on the real input (`refs/tags/v1.79.3` → `a266067`), green on a
matching pair, red on a dirty working tree that claims the right version, and
red on a tag with no version source. The probe is discovered by
`check_probes_pass.py` like the others, and the case is pinned in
`tests/test_check_tag_version.py`.

It also surfaced a class defect on the way. Every gate that runs a Python
checker out of this checkout writes `__pycache__/` into it, and the proven-step
cache keys on that checkout's untracked contents — so a checkout WITHOUT this
repo's `.gitignore` (an installed copy, a CI export) changed identity part-way
through a push, and every step the pre-commit hook had proved re-ran in the
pre-push export. `_proven.sh`'s gates identity now excludes `__pycache__/` by
path segment (a substring filter would silently drop real work, and a file
genuinely named `__pycache__.py` is not a cache). Bytecode is not work in
progress on the gates; a real edit to a checker still invalidates the cache,
and both directions are pinned in `tests/test_proven.py`. Found by adding one
call to `push_gate.sh` and having an unrelated test go red.

## v0.13.4 — two new shared gates, both canaried rather than exempted _(2026-09-30)_

`check_version_provenance.py` asserts that a version string references a git
hash and a build date, and that something sets them. It is STATIC on purpose:
gating on `binary --version` actually printing a hash cannot run on every
repository — wrong architecture, missing toolchain, a build that costs minutes,
a crate with no binary at all — and a check that gets skipped everywhere is
worse than no check. So it asserts the DECLARATION, which is cheap, portable,
and still fails for the reason anyone wanted it: a version written without
provenance. Exit 2 means "cannot tell", which is not the same as pass.

`check_swift_warnings.py` judges one `swift build --build-tests` whose whole
output is read once with every ANSI colour and OSC hyperlink stripped first.
Two earlier gates split the job and neither could do it: the concurrency grep
matched diagnostic group names that `swift build` wraps in terminal hyperlinks,
so the name never appeared whole; and check-deprecated ran its own second build,
which on the cold pre-push tree had nothing left to compile and printed no
warnings at all. "Every warning fatal" was therefore never true at push — found
in ZoneWM 2026-09-26, where a `self` captured in a `@Sendable` closure in a
test, and an unneeded `nonisolated(unsafe)`, both passed it. A build that fails
fails the gate, because files it never compiled printed no warnings.

Both landed UNPROVEN and the calibration ceiling was already spent, which turned
every consumer's push red rather than one. Both are static, so both are
fixtureable, so both are now canaried — red on a violation, green on a clean
tree — which puts the ceiling back where it was instead of raising it to two and
teaching every repo that the number means nothing. `--staged` was added to the
provenance checker because the hook already passed it.

## v0.13.3 — a hook's GIT_DIR never reaches git run on another repository _(2026-09-27)_

Git hands a hook `GIT_DIR` (pre-commit also `GIT_INDEX_FILE`). Under a
LINKED worktree `GIT_DIR` is absolute, so a checker that ran `git init` on a
scratch repo inherited it and re-initialised THE REAL REPOSITORY instead —
and with `extensions.worktreeConfig` on, wrote `core.bare = true` into the
shared config. Every checkout of that repo then died with `fatal: this
operation must be run in a work tree` (zinc, 2026-09-27). These gates run
inside every consumer's hooks, so one miss flips any repo, in every worktree at
once, from a command that looked like it was building a fixture.

Git on the repo BEING gated keeps the variables — `GIT_INDEX_FILE` names the
index being committed, which is exactly the tree to police. Git on any OTHER
repo, and every process run inside one, now drops git's own list from `git
rev-parse --local-env-vars`, never a hand-kept copy (the one in `_proven.sh`
had drifted to 7 of git's 15): `checks/_gitutil.py`'s `foreign_repo_env()`,
used by `check_empty_scope` for the skeleton's init/config/add/commit AND for
the sweep whose gates otherwise measured the real tree, and by
`check_probes_pass` for every `--probe`; `gates/push_gate.sh`, whose export ran
with the pushing worktree's `GIT_DIR`; `gates/_proven.sh`; `goh-testkit`'s
`git_command()`/`git_in()`, which replace four hand-rolled unscrubbed test
helpers; and `tests/conftest.py`, at import, before any fixture.

Pinned by `tests/test_hook_git_env.py` and
`crates/goh-testkit/tests/hook_git_env.rs`: a scratch main, a linked worktree
and worktreeConfig on, with `test_the_fixture_reproduces_the_class` asserting
the raw inheritance really does flip `core.bare` — so a passing test cannot be
the harness being vacuous. Each route was red-proven by removing its own scrub.
Two proofs were blind at first (`--show-toplevel` answers the cwd under a
leaked `GIT_DIR`; the sweep's 100-char echo cut both paths before they
differed), and a census test pins every `Command::new("git")` in `crates/` so a
new unscrubbed fixture helper fails. Contract #12 in `docs/contracts.md`.

**If a repo of yours ever printed `must be run in a work tree`, check it:**
`git config --file "$(git rev-parse --git-common-dir)/config" --get core.bare`
answers `true` for the residue, and the same command with `--unset core.bare`
repairs every linked worktree at once (reproduced and repaired on a scratch
repo for these notes; `git status` fails before, succeeds after).

This release also ships the four stanzas below, which were written but never
tagged: v0.12.6, v0.13.0 (the Rust port), v0.13.1 and v0.13.2.

## v0.13.2 — no process kill by name _(2026-09-27)_

`checks/check_no_kill_by_name.py`, opt-in per repo with
`GOH_NO_KILL_BY_NAME=1`, fails on a `pkill` or `killall` that matches by
name, and on a `pgrep` or `pidof` that feeds a `kill`. A name matches
processes the caller does not own. On 2026-09-23 zinc's T6 cleanup ran
`pkill -9 -f camoufox` and SIGKILLed another session's Gemini browser in the
middle of a reply, which stopped the necrohand campaign. Owner-scoped forms
pass: `pkill -P`, `-g` and `-s`, and `killpg`. Comments, docstrings and prose
are not kills.

Survivors go in `kill_by_name_allow.json`: one exact line in one file each,
with a reason and a status. `legitimate` is a decision. `unreviewed` is debt,
and every run counts it. A stale entry fails.

Seeded the same day in the seven repos without an active session: 52 entries
covering 58 lines, in this repo, CadGoose2, ZeroThunder, necrohand, divoom-control,
homebrew-tap and sys_updater. Surveyed but left to their owners: ZoneWM (8
hits) and koffee_big (4 hits, a tree dirty with someone's work).

The native pipeline delegates this step to the Python checker, as it does
shell lint. Parity is pinned both ways, and GAF's canary proves the check
bites.

## v0.13.1 — a gate over the working tree says when the tree moved under it _(2026-09-27)_

A gate that builds and tests the WORKING tree certified whatever bytes sat
there while it ran: an edit mid-run killed ZoneWM's `make verify` with
`error: fatalError` in a target nobody touched and no cause named, and a
green run over a moving tree certified bytes no commit holds.
`lib/tree_stamp.py` stamps (mtime, size) of every file git would show at the
start and compares at the end; `goh_tree_stamp` (swift, rust and python
gates) refuses a pass over a moved tree and names the move on a red one.
The staged gates do not stamp: they judge the index, so another session's
edit elsewhere is not theirs to refuse. Tool caches (`__pycache__`,
`.pytest_cache`, `.build`, `target`, lockfiles a build writes) are gate
output, never a move -- found by this repo's own py and rust gate tests.
Consumers with their own runner call the CLI (ZoneWM's `make verify`).

## v0.13.0 — the Rust port: native layer 1, bit-exact golden, one resolver _(2026-09-27)_

Every checker a shared gate runs is now native (`goh`) with the Python
checker as its parity-pinned fallback: home-paths, ceiling + ratchet,
no-allow, screen presentation, lints opt-in and the skills corpus joined
emoji / markers / length / secrets (1.6x-15.6x per step). A staged run reads
each index blob once and exports shell files once: 197 git spawns on a
50-file commit became 6 (~2.3 s -> 0.24 s). `goh golden` is bit-identical
to the numpy tier and decodes as Pillow does. `gates/goh.sh <check>` is the
one way a consumer runs a house checker; `gates/_goh_bin.sh` the one binary
resolution. `scripts/build-goh.sh` never publishes `bin/goh` from
uncommitted goh sources (`GOH_BUILD_DIRTY=1` to override), and no test run
can publish it. Plan retired: `git show 15004a5:docs/rust-port-plan.md`.

## v0.12.6 — a hook from an older stock is pristine, not "locally modified" _(2026-09-27)_

`install.sh` refused to update three repos whose hooks had never been
touched: they were on the previous stock hook, installed before the
install record existed, so the hook matched neither the current stock
nor a record. `retired_hooks.sha256` lists the digest of every stock
hook ever shipped (append-only); a hook matching one is overwritten
without `--force`. Found the day v0.12.3's pre-commit change (route
through `tools/gate.sh --staged`) needed to reach every repo -- until it
did, every staged language layer was decorative at commit time there.

## v0.12.5 — a failed coverage export shows its own output _(2026-09-21)_

`coverage_gate.sh --lang rust` sent every `cargo llvm-cov` export to
`/dev/null`. When one failed -- monitor's `local_agent_test`, once, at
push time, and green on every rerun -- the gate said "export failed" and
nothing else, so the defect reached the push with no evidence and had to
be reproduced by hand before it could be looked at. The export's output is
kept beside its part (`part-<pkg>-<kind>-<name>.info.log`) and its tail is
printed on failure. `test_failed_export_shows_its_own_output` pins it
(proven red on the old gate).

## v0.12.4 — the coverage build pins the BUILD dir, not only the target dir _(2026-09-21)_

`coverage_gate.sh --lang rust` isolated the instrumented build with
`CARGO_TARGET_DIR` alone. Since cargo's `build.build-dir` (the house layout
since 2026-09-20 puts every crate's intermediates under `~/.cargo/build/`),
the target dir holds only final artifacts; the test binaries cargo-llvm-cov
exports from live in the build dir, shared with every ordinary build of the
crate. The gate therefore merged the instrumented binaries of the PREVIOUS
source into the report - lines past the end of the current file, all
uncovered - and read a 97.5% tree as 93.5% (routines). The gate now exports
`CARGO_BUILD_BUILD_DIR` equal to its own target dir; the stub-cargo test
records the environment of every build-driving call and refuses any call
that sees a different build dir. Proven red on the old gate.

Also: **the Swift gate asks where the codecov payload is instead of
guessing.** Swift 6.3's build system writes it to
`.build/out/Products/<config>/codecov/`, not `.build/<triple>/debug/codecov/`,
so `swift_gate.sh` reported a green `test + coverage` step followed by "no
codecov payloads match" (antiknob, blocking its push). The gate now passes
`swift test --show-codecov-path`'s answer to `check_swift_coverage.py`
(`--spm-glob`, repeatable), and the checker's default tries both layouts.
Tests: `test_spm_finds_the_swift_6_3_products_layout`,
`test_spm_glob_names_the_exact_payload`.

Also: **`no_allow` sees a suppression wrapped in `cfg_attr`.**
`#![cfg_attr(target_os = "macos", expect(unsafe_code, ...))]` is the
attribute with a condition on it, and the literal grep for `#[expect(` never
saw one; monitor carried eleven that way. The wrapped form is matched by the
`allow(`/`expect(` token inside an open `cfg_attr(` attribute, across
rustfmt's multi-line layout, with string literals ignored. Tests:
`test_cfg_attr_wrapped_suppression_is_refused`,
`test_cfg_attr_without_a_suppression_passes`.

## v0.12.3 — the commit hook goes through `tools/gate.sh --staged` _(2026-09-21)_

`hooks/pre-commit` exec'd `structural.sh --staged` directly, so a staged
language layer wired into a repo's `tools/gate.sh` (which the starter invites:
"the cheap half of a language gate runs at COMMIT time") never ran on commit -
a commit gate weaker than the push gate, the hole rule 14 names. Found in
media_server when a Rust gate under `--staged` was added and a crate's red
gate still committed. The hook now execs `tools/gate.sh --staged` when the
file exists (structural alone otherwise), symmetric with pre-push. Test:
`test_pre_commit_runs_the_repo_gate_staged_layer` (a layer's marker must
appear on commit; a red layer must block it) - red before the fix. Existing
repos pick the new hook up on their next `install.sh` (stock hooks are
replaced; locally modified ones are refused as before).

Also: the repo's own `crates/goh/tests/common/mod.rs` carried six
`#[expect(clippy::expect_used)]` - exactly what v0.12.2 started refusing, and
the push gate said so. The fixtures are now `crates/goh-testkit`, a
dev-dependency crate whose manifest states the test policy `clippy.toml`
already declares for test code (`allow-*-in-tests`) but cannot reach helper
functions in an integration-test crate; one `must()` helper stops a test with
the reason. No suppression attribute anywhere in the tree.

## v0.12.2 — `#[expect]` is a suppression too _(2026-09-20)_

`checks/check_no_allow.py` now refuses `#[expect(...)]` and `#![expect(...)]`
beside `#[allow]`, and its scope covers `tests/` and `examples/` as well as
`src/`, `benches/` and `build.rs`. `#[expect]` cannot rot (it errors when the
 lint stops firing) but it still ships the finding - a cast that "fits today",
 a function that is "only 104 lines", a `#![allow(dead_code)]` on a shared
 test fixture that should be a dev-dependency crate. Policy from the servers
 Rust campaign (operator, 2026-09-20): fix the finding properly. Tests:
 `test_expect_is_refused_too`, `test_tests_dir_in_scope` (the two inverted
 cases from v0.12.1's suite).
- **MCP handshake LATEST is 2026-07-28 (SEP-2575).** `lib/mcp_scaffold.py`
  answers `initialize` with 2026-07-28 when asked, echoes every version in
  `HANDSHAKE_VERSIONS`, and falls back to LATEST on an unknown or missing
  version (three new cases in `tests/test_mcp_scaffold.py` pin each arm).
  Consumers inherit it at runtime via `GOH_DIR` with no reinstall:
  koffee_big, necrohand and ZeroThunder speak the new version on their next
  run. 2025-11-25 stays supported (still echoed), just no longer latest;
  nothing in docs/ or lib/ pinned it as latest, so nothing else rotted.
- **Commit-time ruff over staged Python** (`gates/py_staged.sh`, wired by
  `install.sh`): the cheap half of `py_gate` runs at commit time, so a
  lint finding surfaces one commit earlier instead of one push later.

## v0.12.1 — a named "not applicable" skip is not a pass _(2026-09-14)_

Patch. `check_empty_scope.py` counts a gate that exits 0 having printed
"not applicable" as a non-run, not as compliance over an empty tree. A
macOS-only gate on a Linux runner (monitor's codesign check) had no honest
place before: an excuse would be true on one host and false on the other,
and the stale-excuse ratchet fires either way. Probe case added; verified
on Linux in a container.

## v0.12.0 — the emoji gate reads escapes; the binary is measured end to end _(2026-09-14)_

Minor: a behaviour every consumer inherits on its next run.

- **Escaped codepoints are emoji too.** A Rust brace escape, a Python
  eight-digit escape or a JS four-digit escape that names a forbidden
  codepoint renders as one, and both checkers -- Python and native -- were
  blind to it (monitor's retired repo-local checker was not; that is where
  the class came from: two padlocks in a config panel). Both report the
  escape at the column of its backslash, marked "(written as an escape)",
  in the same order per line; parity and unit cases pin it. The first run
  over the fleet found 22 in CadGoose's UI strings (now the Kare set), and
  test fixtures in four repos that had been writing emoji as escapes to
  dodge the gate -- they are built from codepoint numbers now, which is
  neither a glyph nor an escape. Where an escape is legitimately the
  product (CadGoose's goose icon), `GOH_ALLOW` says so by name.
- **`crates/goh` is measured at 97.9% (floor 95, the house number).**
  `crates/goh/tests/cli.rs` drives the binary over fixture repos -- every
  subcommand, both scopes, every `.gatesrc` knob -- where `cargo llvm-cov`
  can see it; the Python parity suites had been giving it that exercise
  from pytest, where it could not.

## v0.11.1 — the Linux path, exercised _(2026-09-14)_

Patch: fixes to what v0.11.0 shipped, each found by a consumer's CI or by
running the release rules against this repo itself.

- **Portable `mktemp`.** `mktemp -t name` is BSD syntax; GNU mktemp needs an
  `XXXXXX` template, so the structural, manifest and swift gates died on the
  first Linux CI runs that called them by name. Fixed in one place; the
  claim of Linux support is now exercised by two consumers' CI.
- **`goh --version`**, checked by `scripts/build-goh.sh` against `Cargo.toml`
  and pinned by an integration test: an installed binary that cannot say
  what it is cannot be verified as the one just built.
- **Release kit: `--archive-build CMD` and `--verify CMD`.** Build from
  `git archive HEAD` (tracked files only -- what a tarball consumer gets)
  and install-and-exercise before the tag. ztools and sys_updater delegate
  to the kit again instead of carrying their own sequencing.
- `loc_of_baseline_files.py`: a sentinel-only baseline ratchets to a pass.

## v0.11.0 — the native gate is the gate; the house checks reach every repo _(2026-09-14)_

Minor: new behaviour every consumer inherits on its next run. What was WRONG
before, in the order a consumer would meet it:

### `.gatesrc` written as `export KEY=value` was invisible to the native binary

Three repos write their keys with the bash `export` idiom. The Python side
sources the file and saw them; the native parser keyed on the literal
`export GOH_EXCLUDE` and silently dropped the value -- ztools' vendored
crate passed under Python and failed under `goh`. The prefix is stripped;
proven red against the old parser.

### The no-`#[allow]` check ran only where a repo had copied it

`rust_gate.sh` looked for a repo-local `tools/check_no_allow.py` and warned
and skipped when absent. Four repos carried copies (one already behind);
every other Rust repo was never checked. The step now runs
`checks/check_no_allow.py` from here, unconditionally, and honours
`GOH_EXCLUDE` like every other house check (a vendored crate is exempt with
one line). The copies in antiknob, divoom-control, routines and ztools are
deleted.

### Length ceilings were required but never enforced here

`GOH_LINE_BASELINE` made the structural gate verify that every cap-exempt
file CARRIES a ceiling, and left the shrink-only ratchet to each repo's own
gate script -- a repo that listed ceilings and never wired the ratchet was
bounded by nothing. Both pipelines now run `check_baseline_ratchet.py` over
the baseline's files (`checks/loc_of_baseline_files.py` measures them; a
sentinel-only baseline ratchets to a pass). Proven red on one line of
growth, native and Python agreeing.

### Bin-only crates read as "no coverable lines"

`coverage_gate.sh` exported lib and test targets only, so a crate whose
unit tests live in its bin (`crates/goh` is one) could not be measured. Bin
targets are exported too; part files carry the target kind.

### The parity suites ran a binary that could vanish under them

Five module fixtures each built `goh` and executed it at its uplift path in
the machine-wide cargo target dir, where a concurrent relink (another xdist
worker, another repo) opens a window in which the path does not exist. One
session fixture builds once and hands out a private copy.

### This repo gates itself the way it gates everyone

`GOH_CI_STEPS` in `.gatesrc`: the house Rust gate over `crates/goh` with a
stated coverage floor (55%, the unit tests alone -- the parity suites drive
the binary where llvm-cov cannot see it), a biting `cargo audit`, the
structural gate, `cargo test`, and the pytest suite via `tools/pytest.sh`;
`tools/gate.sh --full` delegates to `local_ci.sh`. `clippy.toml` exempts
test code from the restriction lints, retiring five per-test `#[expect]`s;
the duplicate `tools/rust_gate.sh` is gone.

The rest of this release, from earlier in the cycle:

### The native `goh` binary carries layer 1

`gates/structural.sh` now execs `bin/goh` when `install.sh` has built it
(cargo present; `GOH_SKIP_BUILD=1` to skip) and runs the Python checkers,
saying so once, when it has not. `GOH_BIN` names a binary explicitly — a
pointer at nothing is reported, never silently replaced; `GOH_NO_NATIVE=1`
forces the Python path (the parity suites use it, so native is always
compared against Python, never against itself). `scripts/build-goh.sh` gates
the platform first (64-bit only, macOS Apple silicon only, Linux x86_64 and
aarch64 kept) and lands the binary atomically. Measured on ztools: 1.0 s
native against 1.5 s Python for the full structural gate.

### Full scope is the worktree

`listed_files` (both `checks/_gitutil.py` and `crates/goh/src/gitutil.rs`)
lists `git ls-files --cached --others --exclude-standard` for full runs:
tracked plus untracked-but-not-ignored. It was tracked-only, so a brand-new
oversized file was invisible to `ci.sh` / `--full` until it was staged —
twice in one day across two repos. Staged scope is unchanged (the index).
The empty-scope skeleton ignores its copied gate dir through
`.git/info/exclude` so the sweep stays blind to it; two excuses whose reasons
had stopped being true (`check_lints_optin.py` — this repo has had a Cargo
workspace since the `goh` crate landed — and `check_probes_pass.py`) are
deleted, as the ratchet demanded.

### Per-target Swift coverage floors, without the reroute

`check_swift_coverage.py` gains `--floors-json` (schema shared with
`coverage_gate.sh`), wired into `swift_gate.sh` as `GOH_SWIFT_COV_FLOORS`.
A package's overall percentage is dominated by whichever target has the most
lines -- on a SwiftUI app that is the views, which no unit test executes --
so the package number can sit above its floor while the target holding all
the logic rots.

A floors key is a path PREFIX relative to `Sources/`, matched segment-wise,
not only a target name. A single segment behaves exactly as a target name did,
so existing floors files are unaffected; a deeper key (`App/Core`) floors part
of one target. That is what makes it unnecessary to split a package into logic
and view TARGETS purely so a floor can be aimed -- a split that forces `public`
onto every type crossing the new module boundary. A directory is enough to aim
a floor at, and letting the coverage tool dictate module structure is the wrong
way round.

**A target named in a floors file that matches no measured source is now a
hard error in every implementation.** Both call sites in
`gates/coverage_swift.py` scored an unmatched target 100% and passed it, so
renaming or misspelling a target turned its floor into one that could never
fail. A floors file is the thing a reader trusts to say what is enforced; an
entry that enforces nothing has to be loud. A floors file that parses to no
floors at all is likewise a config error rather than an empty ratchet.

### The Swift coverage gate measured the wrong tree, with the wrong binary

Two defects in `gates/coverage_swift.py`, found while trying to route
`swift_gate.sh` through `coverage_gate.sh` so per-target floors would work.
The reroute is NOT done, because measuring the target first showed it would
have made every repo's Swift floor easier to pass.

**It could not measure an SPM package at all.** An `.xctest` bundle built
with debug symbols -- which is how `swift test` builds by default -- holds a
`.dSYM` DIRECTORY beside the executable in `Contents/MacOS`. The binary
selection took the first glob hit, handed llvm-cov the `.dSYM`, and llvm-cov
died with "Is a directory". The xcodebuild path had the same defect through
`pick_binary`. Both now select regular executable files only.

The existing selection tests missed it because their fixtures put the
executable in `Contents/MacOS` and nothing else. A harness that builds its
own inputs only ever tests the shapes it thought to build.

**And the failure named the wrong thing.** llvm-cov exiting non-zero was
reported as "could not parse llvm-cov JSON summary", with llvm-cov's own
stderr discarded -- so the one line that said `Is a directory` never reached
anyone. It now reports the exit code, the binary, and what llvm-cov said.

**Tests/ counted toward the floor.** `coverage_swift.py` applied no scope
filter, while `checks/check_swift_coverage.py` excludes test sources,
generated runners and DerivedSources. On the same tree (antiknob) the two
read 10.54% and 4.78%. A test file is ~100% covered by definition -- it is
the thing doing the running -- so a floor set on the first number can be met
by writing tests that assert nothing, which is the exact hole the checker
was fixed for on 2026-09-07.

One copy of a rule and one ABSENCE is worse than two copies: the second path
looked like it agreed and did not. The definition now lives in
`lib/swift_coverage_scope.py` and both import it, with a test that fails if
either re-grows a local copy -- and a behavioural test that the filter is
actually applied, because an earlier version of that guard asserted only
that the import was present and stayed green when the filter was deleted.

`gates/coverage_swift.py` passed the 500-line cap on the way; the
`cov:ignore` marker machinery moved to `gates/coverage_markers.py`.

## v0.10.0 — gates that reported clean about things they never looked at _(2026-09-07)_

Nineteen commits, one theme. Every gate below was GREEN over a subject it had
not read, and in each case success and vacuity printed the same word.

### The empty-scope campaign

**A gate that inspected nothing has not passed, it has abstained.**
`checks/check_empty_scope.py` runs every gate against a SKELETON TREE — the real
gate directory, the whole directory layout, no files — and fails any that reports
compliance over an empty population. Grepping for the guard was tried first and
was wrong in both directions: of 22 gates it accused three that were fine and
missed all three that were really blind, because the absence of a guard is not
visible in source. The scan is right, the comparison is right, and the
collection it iterates is simply empty.

Two harness details decide whether the sweep means anything: the gate directory
is COPIED so its baselines and allowlists come with it, and the copy is NOT
tracked — tracked, every whole-repo scanner finds the copied gates, does real
work on them and passes honestly, which reports as blindness.

Related fixes from the same sweep, each its own commit:

- **`_gitutil`: a failing git was reporting an empty repo, and every gate
  believed it.** One helper ran git without checking its exit code, so a corrupt
  index returned `[]` and every consumer printed "OK — 0 tracked files clean".
  Three "blind gates" were ONE defect.
- **`check_baseline_ratchet`: every entry vanishing is not every ceiling met.** A
  shrink-only ratchet has no ceiling left to exceed once its population reaches
  zero, so a total collapse read as the best possible result — one printed "24
  entries within ceilings" while NAMING all 24 as vanished, and exited 0. A
  populated baseline with an empty measurement now FAILS, and the success line
  reports what was MEASURED rather than what the baseline remembers. An empty
  baseline stays exempt, or the ratchet is unadoptable by a project starting
  from zero.
- **`check_tests_registered`: "all 0 test file(s) are registered" is not a pass.**
- **The self-proofs now RUN, because nothing ever did**, and a calibration
  registry records which shared gates have been PROVEN able to fail. Blindness
  and unprovenness are different defects: three gates in the best-scoring repo
  were 8/8 proven and still blind, because a probe shows a gate can fail on a
  VIOLATION and says nothing about an ABSENT subject.
- **`probes`: a `main(argv)` dispatch is a self-proof, and it was being missed.**

### Swift coverage counted the wrong sources

`fix(swift-cov)` twice: it reads `llvm-cov export` 3.x, and it stopped counting
TEST sources and GENERATED sources toward the coverage number. A floor computed
over the tests that are supposed to satisfy it measures nothing about the code.

### The disk watch left the repo, not just the gate

It had been moved out of CI while still living here; now it lives where it runs.

### The two ways a clippy gate lies

From a lint-adoption campaign across four Rust repos (monitor, routines,
ztools, divoom-control), all four taken from "no policy at all" to zero at
`-D warnings`. Two of the findings were structural rather than per-repo, which
is what made them belong here.

**`checks/check_lints_optin.py`** — a workspace's `[workspace.lints]` is a
DECLARATION; a member crate applies it with `[lints] workspace = true`, and one
that never says so inherits nothing. There is no warning anywhere: cargo does
not mention the omission and `clippy -- -D warnings` passes, because the crate
genuinely has no findings AT THE LEVELS IT IS SUBJECT TO. Measured on `monitor`:
the workspace had declared `pedantic` and `nursery` from the start, two of three
crates opted in, and the third — the biggest, the one uploaded to every
monitored host — had accumulated 254 findings that every green gate had agreed
were absent. Calibrated by removing that crate's opt-in and watching the check
go red. Six tests, including the empty-population case: a workspace with no
policy is NAMED as such rather than reported as a pass.

**`GOH_RUST_LINT_CONFIGS` in `gates/rust_gate.sh`** — the gate's clippy step
lints ONE cfg: this machine's target, all features on. A crate that is part
`cfg(target_os = ...)` or part `cfg(feature = ...)` has halves that command
never compiles and therefore cannot report on. On `monitor` that meant a Mac
checked one half and the ubuntu runner checked the other, neither failing on the
other's code and nothing comparing them: 26 findings on the host, 48 for
linux-musl, ten of them in src the host cannot see — plus two `#[expect]`s that
were UNFULFILLED there, which under `-D warnings` is an ERROR. The agent's build
was broken for the platform it ships to while every gate was green.

Each entry is extra cargo argv, `:`-separated. A `--target` whose std is not
installed is a HARD FAILURE naming the `rustup target add`, never a skip — a
step that inspects nothing must not read as a pass, which is this estate's
oldest gate defect. Calibrated both ways: with a Linux-only `#[must_use]`
removed, the existing clippy step stays GREEN and the new one goes RED.

**`gates/rust_manifest_gate.sh` remedy advice corrected.** It told the reader to
silence a finding with

    [lints.cargo]
    unused_dependencies = "allow"

which cargo does not support on stable — it is an "unused manifest key", so
cargo emits `(manifest) generated 1 warning`, which is precisely the string this
gate greps for and fails on. Following the gate's own advice made the gate fail.
Measured on cargo 1.98.1 with both `allow` and `deny`; the advice had never been
run. It now says to remove the dependency, explains why `[lints.cargo]` is a
trap, and points at `cargo-machete`, whose ignore list lives under a metadata
key cargo does read.

### An exemption from the CAP is not an exemption from every bound

**`checks/check_exclusion_has_ceiling.py`** — `GOH_LINE_EXCLUDE` and the
shrink-only ratchet are separate mechanisms with separate lists, and nothing
compared them. Found in `monitor`: a 619-line test file named in LINE_EXCLUDE
(so the cap did not apply) and absent from `loc_baseline.txt` (so no ceiling
applied either). Bounded by nothing — and the repo's baseline header asserted the
opposite in prose, which is why nobody looked. **A document that describes a
property nothing enforces is worse than silence, because it answers the question
that would otherwise get asked.**

Enabled per repo with `GOH_LINE_BASELINE`; a repo that sets LINE_EXCLUDE without
it is WARNED rather than passed silently. `GOH_LINE_UNBOUNDED` names the
exemptions that legitimately need no ceiling — captured vendor material that must
stay emoji- and secret-scanned but is not ours to split — because the first
version fired on a repo using the key correctly, and a warning on every commit
that the reader is meant to ignore is how a gate stops being read. Both lists are
checked for staleness: a waiver matching no tracked file FAILS.

Calibrated against the real hole: with the file reconstructed and exempted, the
cap says "OK — 302 files within 500 lines", the ratchet says "OK — 7 entries
within ceilings", and only this check exits 1.

Also extracts **`_gitutil.line_count`**. `check_file_length` had the convention
right (a trailing newline TERMINATES the last line) and the new check was written
with a plain `count + 1`, so at exactly 500 lines the two gates would have
disagreed about the same file — and a disagreement between gates reads as a
defect in the file.

### `cargo machete` — the unused-dependency gate that actually runs

`[lints.cargo] unused_dependencies = "deny"` reads like a gate and enforces
nothing: the namespace needs `-Zcargo-lints` on nightly, so on stable cargo
prints an "unused manifest key" line and exits 0. It had never been enforced on
any toolchain in the repo that declared it. `cargo-machete` does the job on
stable and exits 1 on findings, so `gates/rust_gate.sh` treats it as a hard
failure, with a NAMED skip when the tool is absent.

Calibrated on `routines`: clean, then an unused `heck` added and the step goes
red naming it, then clean again. Across the estate it found four real unused
dependencies (monitor, app_updates). Its blind spot is ident-based scanning, so
exemptions are local, in the crate's own `[package.metadata.cargo-machete]`,
with the reason.

### `check_skills_corpus.py` — and then a caller for it

A skills corpus is authored prose that nothing compiles, so its defects are
silent. Six checks: frontmatter, name/directory agreement, `[[wikilink]]`
resolution, reference-link resolution, a word ceiling, and duplicate
lesson-shaped titles. Each found a real defect on its first run.

It then had **no caller** — no repo set `GOH_SKILLS_CORPUS`, so the gate written
to catch silent defects was itself running nowhere, which is the class it exists
for. Wired into this repo's own `.gatesrc`, because `~/.claude/skills` is a
global asset with no repo of its own.

**An external corpus that is ABSENT is a named skip, not a hard failure.** The
first wiring hard-failed wherever `HOME` is not the developer's — which is exactly
what this repo's own self-host test does, committing a copy with `HOME` set to a
temp dir to prove `.gatesrc` stays hermetic. The test was right. Calibrated all
three branches: present and broken exits 1, present and clean exits 0, absent
warns and exits 0.

## v0.9.0 — eval findings 1-10 _(2026-09-04)_

Every item from EVAL-2026-09-04.md, fixed test-first, measured throughout.

**What shipped**:
- Layer 1 shell lint: `checks/check_shell_lint.sh` (`bash -n` always;
  `shellcheck --severity=error`, missing binary degrades to a named
  warning). Wired into `structural.sh` both scopes. Found two live bugs
  on adoption (a prose `# shellcheck ...` comment parsed as a directive;
  a sourced lib missing its shell directive) — both pinned by tests.
  shfmt deliberately excluded (tree uses aligned continuations).
- Rust coverage floor: `rust_gate.sh` runs `coverage_gate.sh --lang rust`
  when `GOH_COV_FLOOR_RUST`/`GOH_COV_FLOORS_JSON` is set (explicit argv —
  `.gatesrc` values are not exported, found by the red test), warn nudges
  otherwise like py/swift gates.
- Secrets gate: `checks/check_no_secrets.py` (known prefixes + key
  headers, staged + full, `secret-ok: <reason>` markers; bare markers
  suppress nothing). No entropy heuristics by design. Tree scans clean.
- Swift parity, not merge: `tests/test_swift_coverage_parity.py` runs the
  legacy helper and the llvm-cov engine against one 50% fixture and pins
  identical verdicts (red-proven). A merge needs a real-swift oracle that
  does not exist here.
- `docs/contracts.md` #11: `.gatesrc`-as-shell trust posture recorded.
- UX: `GOH_LCI_TIMEOUT` per-step ceiling for `local_ci.sh` (TERM/KILL +
  subtree sweep, exit 124, non-numeric is exit 2); `--help` for
  `install.sh`, `tools/gate.sh`, `structural.sh`; `gates/doctor.sh`
  (wiring diagnosis: GOH resolution, hooksPath, unknown `.gatesrc` keys
  derived from `docs/config.md`, toolchain presence) + `--doctor` in
  both gate entries.
- `GOH_TIME=1`: per-step elapsed seconds on ok lines (`_common.sh`).
- `docs/BACKLOG.md`: the rule-#13 file, seeded with deferred items.
- `slow` marker: the 28s real-swiftlint test opts out of the fast loop
  (`-m "not slow"`); the gate still runs everything.
- `lib/mcp_schema.py` split out of `mcp_scaffold.py` (496 → 422/113
  lines); block moved byte-identically, re-exported so downstream
  imports keep working; ruff hits fewer than HEAD (5 → 4).
- Trim: fake-gh stub deduped to `conftest.py`; dup import pruned; stale
  `ci_local`/`loadfile`/count references fixed across docs.

**Bugs the work itself caught**:
- `X | Y` annotations crash OS python3 3.9 at def time (the documented
  class, reintroduced in two new files) — caught by the self-host
  dogfood test; fixed with `from __future__ import annotations` plus a
  one-line class fix in `check_disk_hygiene.py`.
- Parallel-suite race, take two: release-hardening's mid-run edit vs
  release runs (fixed by xdist grouping, contract #10); my own
  mutate/restore red-proof raced `__pycache__` (sleep-separated rerun).

**What deliberately didn't ship**:
- Entropy-based secret detection, shfmt enforcement, swift merge,
  launchd schedule for the disk watch (all in `docs/BACKLOG.md` with
  unblock conditions).

## v0.8.0 — disk watch out of CI, pooled du, parallel suite _(2026-09-04)_

Measured first: full `structural.sh` was 17.7s, of which `check_disk_hygiene`
was 15.1s (a du stat-storm: $TMPDIR 7.4s + 37GB shared cargo-target 4.1s +
project targets 1.5s). The checkers themselves total 0.45s — a Rust rewrite
would speed up the wrong 3%.

**What shipped**:
- Disk hygiene OUT of the gate: `structural.sh` no longer runs it on any
  scope (full tree now ~0.5s, was ~17.7s). Pinned by
  `test_no_gate_runs_disk_hygiene` — re-adding a gate reference fails red.
- The watch lives on as `~/Projects/scripts/bin/disk_hygiene.sh` (thin
  wrapper, same delegation via `GOH_DIR`), unified with
  `reclaim_build_space.sh`: a failure names the reclaimer as the fix.
  Covered by `scripts/tests/test_disk_hygiene.sh` (both directions).
- `check_disk_hygiene.py`: one ThreadPoolExecutor over ALL roots (two pools
  still stacked the slowest scratch root on the slowest cache dir — 12.4s;
  one pool: 15.1s → ~8s) + `GOH_SCRATCH_ROOTS` seam for scoping/tests.
- Parallel suite: `tools/gate.sh --full` uses `pytest -n 8 --dist loadgroup`
  when xdist is installed (loadgroup keeps xdist-grouped files on one
  worker; ungrouped files spread by load), serial + warn otherwise.
- Trim: the 36-line fake-gh stub duplicated in both release suites now lives
  once in `tests/conftest.py` (`test_release_kit.py` 497 → 456 lines);
  pruned a duplicate `import pytest`; fixed stale `ci_local.sh` name and
  `loadfile` references across docs.

**What deliberately didn't ship**:
- No Rust rewrite (evidence says it buys nothing here).
- No launchd schedule for the watch (manual/periodic by hand for now).

**Tests**: 3 new (wiring removal-pin + config-schema seam coverage via
existing tests + scripts wrapper suite); 442 pass in ~40s parallel
(`-n 8 --dist loadgroup`), was 441 in ~168s serial. Baseline gate: full
`structural.sh` 17.7s → 0.5s; standalone watch ~8s (was 15.1s in-gate).

## v0.7.0 — LLM-navigability pass _(2026-09-04)_

Everything from the review of what slows an LLM down here, fixed
additively — no gate behavior changed, no key renamed.

**What shipped**:
- `AGENTS.md`: agent entry point (commands, layout, rules, config pointer).
- `docs/map.md`: every gate/checker/lib/tool with purpose + pinning test;
  coverage-file overlap (`coverage_gate.sh` vs `coverage_swift.py` vs
  `check_swift_coverage.py` vs `lcov_merge.py`) resolved in prose.
- `docs/config.md` + `.gatesrc.example`: single schema for all ~30 `GOH_*`
  keys (README documented ~10); defaults, readers, and the
  `GOH_EXCLUDE` ∪ `GOH_LINE_EXCLUDE` union rule.
- `tests/test_config_schema.py`: drift gate — a new `GOH_*` key without a
  `docs/config.md` row goes red (red-proven with a synthetic key).
- `docs/contracts.md`: nine load-bearing invariants, each naming its test.
- `docs/new-checker.md`: test-first contributor cookbook (fixture helpers,
  `chr(0x...)` rule, staged/index testing, wiring + schema obligations).
- Cutover: `README.md` points at the new docs; `install.sh` starter
  `.gatesrc` points at `docs/config.md` / `.gatesrc.example`.

**What deliberately didn't ship**:
- No `GOH_*` renames, no coverage-file merges, no hook behavior changes.

**Tests**: 2 new in `tests/test_config_schema.py`; 441 total pass.
Baseline before the pass: 439 pass.

## v0.6.0 — coverage strictest convergence, disk cargo-cache watch, MCP Tax parity _(2026-08-26)_

Convergence to the strictest coverage standard across toolchains, third-class
disk leak monitoring, and MCP Tax parity. 399 → 439 tests.

Coverage convergence to strictest standard:
- `coverage_gate.sh`: positive inclusion filtering before ignore (`--include` /
  `GOH_COV_INCLUDE_RE`), per-target and per-file floors with tolerance/exempt
  rules (`--floors-json` / `GOH_COV_FLOORS_JSON`), and shrink-only forgiven-lines
  ceiling (`--marker-ceiling` / `GOH_COV_MARKER_CEILING`). Backward-compatible
  with single `--floor`.
- `coverage_swift.py` & `lcov_merge.py`: include filtering for llvm-cov export /
  parse_xccov, multi-shape floor config loading, per-file floor enforcement with
  stale-exempt detection, and shrink-only marker ceilings.

Disk hygiene, local CI & release kit:
- `check_disk_hygiene.py`: watches shared `CARGO_TARGET_DIR` (`~/.cache/cargo-target`)
  and per-project targets for unbounded cache growth (`--max-cache-dir-gb` /
  `GOH_MAX_CACHE_GB`, default 50 GB) with largest-child diagnostics.
- `local_ci.sh`: preserves failing step log directories on exit so
  reproducibility seeds printed at top of output are retained.
- `release.sh`: unsets `GOH_RELEASE_BUFFERED` after startup snapshot so nested
  invocations and gate test runs remain hermetic and self-buffering.

MCP scaffold & eval transport:
- `lib/mcp_scaffold.py`: Tax SDK parity with server instructions support, input
  schema validation before tool handlers (`-32602 INVALID_PARAMS`), protocol
  version negotiation (`HANDSHAKE_VERSIONS` / `LATEST`), and `tool_from_function`
  helper. Server-death crash class closed and JSON-RPC conformance pinned.
- `lib/eval_transport.py`: Ollama environment variable aliasing (`OLLAMA_API_BASE` /
  `OLLAMA_HOST`, `OLLAMA_MODEL`) as fallbacks behind EVAL / OPENAI keys.

## v0.5.0 — second adversarial pass _(2026-08-25)_

Re-review of v0.4.0 by three fresh hunters (fixes-as-hostile-code, oracle
fuzzing, untouched surfaces) plus refute-first verification. 399 tests.

Fail-open closed:
- `goh_init`'s EXIT trap erased crash exit codes — a gate with a syntax error
  exited 0 and a real consumer commit shipped silently green. Exit 0 is now
  honored only after the completion sentinel; any other path re-raises.
- `coverage_gate` laundering hole: a failing export that left its output file
  behind passed completeness ("100%" over garbage). Success now requires
  per-target `.ok` markers; the redundant count check is gone.
- `py_gate`'s bare `--cov` floored only modules the tests happened to import —
  a never-imported module at 0% passed `GOH_PY_COV_MIN=100`. Coverage is now
  scoped to the package.

Fuzz-proven hardening (oracle oracles, thousands of seeded cases):
- PNG fallback decoder: all corrupt-input escapes are named preconditions
  (were raw tracebacks); **zero wrong-pixel decodes across 12k corruptions** —
  the load-bearing property, now pinned.
- lcov merger extracted to `gates/lcov_merge.py`: BOM'd part files no longer
  silently drop their first record (a real 75% showed as green 100%); FN
  format detected by content; malformed records exit 2 naming file:line.
- Baseline ratchet rejects huge-int JSON (OverflowError traceback) and
  liberal numeric literals in line baselines.
- `local_ci` steps run from the repo root; stdin isolation regression-pinned.

Also: screen-check line lists derive from `\n` (control characters crashed
the probe); `check_no_allow` depth-counter scan fixes an FP and a found FN;
coverage_swift pairs profdata with mtime-nearest binary and refuses ambiguity;
wiring meta-gate parses guarded lines (typo'd refs can't ship blind);
NO_COLOR honors the spec; styled TUI output routes warnings to stderr with
data-safe printf; multiple xcodeproj candidates die instead of locale-picking;
emoji ranges extended (geometric shapes, astral forward-compat) with zero
blast radius across consumers. Consumer forks (divoom ×2, monitor,
koffee_big) got the round-one `-z` fix too.

## v0.4.0 — adversarial review: every finding fixed _(2026-08-25)_

Three independent hunters + refute-first verifiers over checks, shell gates and
consumer seams; 19 confirmed findings, all fixed with regression tests built
from their repros. 289 → 355 tests.

Gate-bypasses closed:
- `_gitutil` now lists paths NUL-delimited — quoted non-ASCII filenames were
  silently skipped by EVERY checker in staged AND full mode.
- `local_ci` steps no longer inherit the loop's stdin — a stdin-reading step
  swallowed all remaining steps and reported "all passed" exit 0.
- `coverage_gate`: a per-target lcov export that fails is a hard failure naming
  the target (rust + cpp) instead of a warn over partial measurement.
- `check_swift_coverage` SPM mode refuses payloads that parse to zero
  measurable files (was: "100% OK", exit 0).
- golden tolerances and ratchet values reject NaN/Infinity (NaN defeated both
  in every direction); non-finite is a named precondition, never a verdict.
- `check_no_emoji` adds the singleton emoji codepoints outside scanned ranges;
  ©®™ documented as permitted typography — bare forms pass, VS16 presentation
  still fails.

False positives removed:
- `check_no_screen_presentation`: single left-to-right state scanner — `//`
  inside a string no longer blanks real code later on the line, and `/*` in a
  string can't open a fake block span.
- `check_no_allow` ignores comment mentions of `#[allow]` (the prose a cleanup
  PR writes), still catches real attributes.
- `coverage_gate` merger parses both lcov FN formats; three-field spans bound
  by parsed end (two-field legacy behavior pinned byte-identically — moving it
  would shift consumer coverage numbers).

Half-states / hardening:
- `release.sh`: stale-tag at an older commit hard-fails naming both SHAs;
  awk stanza matching no longer eats regex backslashes via `-v`; artifact
  fetch failures name the step.
- `install.sh` records installed-hook hashes — re-running bootstrap no longer
  clobbers hand-extended hooks; `--force` overrides.
- `mcp_scaffold` returns isError for unserializable tool results instead of
  dying mid-session; subprocess timeouts kill whole process groups
  (`lib/killtree.py`); disk hygiene measures through partial du failures and
  checks free space per scratch root; local_ci runs steps from the repo root;
  profiling scripts carry line-1 shebangs.
- Self-host: GOH's own `--full` now runs its test suite — a gate bug can no
  longer reach eleven consumers without failing here first. README documents
  `git commit --no-verify` as the escape hatch.

## v0.3.0 — post-unification hardening _(2026-08-25)_

- `checks/check_no_screen_presentation.py`: bare-identifier matches in Swift
  TYPE position (annotations, params, returns, casts, generics) no longer
  flag; use positions (`NSScreen.main`, `.screens`) still do. Erases the
  fake-protocol-surface markers ZoneTilerWM reported.
- `tools/release-kit/release.sh` hardening: `--no-push` implies skipping the
  GitHub-release step; the script self-buffers at startup so a concurrent
  edit can no longer corrupt a running invocation (field-reported as a silent
  exit 0 — worse than a crash); stanza extraction keeps `###` subsections and
  tags carry them via `--cleanup=verbatim`; X.Y versions and the
  missing-CHANGELOG guard are pinned by regression tests.
- `gates/swift_gate.sh` + `gates/swift_lint_baseline.py`: `GOH_SWIFT_LINT_BASELINE`
  turns the lint stage into a shrink-only ratchet — baselined violations tolerated,
  NEW ones fail named, vanished ones are a re-record nudge. Match key (file, rule,
  reason) probed against swiftlint 0.65.1: code motion and severity flips stay
  tolerated, reason drift and other-file twins do not. Unset keeps bare strict
  linting. Red-proven both directions.

## v0.2.0 — the harness unification _(2026-08-25)_

Maximalist centralization: sixteen per-repo harness families moved under one
roof. 74 → 262 tests, every new checker red-proven.

- `lib/desktop_lock/`: canonical machine-wide desktop mutex relocated from
  `~/Projects/scripts/lib` (PID+start-time record contract preserved verbatim).
- `checks/check_baseline_ratchet.py`: shrink-only ceilings (JSON or line
  baselines) replacing monitor/necrohand/ZeroThunder ad-hoc ratchets.
- `checks/check_generated_fresh.py`: artifact-freshness gate (regenerate to a
  temp sandbox, hash-compare, timeouts mandatory) — the Taxes pattern,
  generalized.
- `checks/check_no_screen_presentation.py` + `lib/headless_env.sh` +
  `checks/check_no_screen_linkage.sh`: the two-halves screen invariant —
  static grep (absorbed necrohand's full pattern set, differential 3/13 →
  14/14), runtime env contract, and an nm -u link-table audit.
- `checks/check_tests_registered.py`: every test source on disk must be
  registered inside an add_executable/add_test block (stronger than the
  CadGoose/CadGoose2 verbatim twins it replaces).
- `lib/golden_core.py`: shared pixel-diff core (mean abs
  diff, changed fraction, SSIM; Pillow-or-pure-Python); blessing stays repo
  policy by design. Offscreen-render cookbook recorded in `docs/harnesses.md`.
- `lib/eval_transport.py` + `lib/mcp_scaffold.py`: grader/model-agnostic eval
  transport (parse-rate guardrail, atomically resumable sweeps) and stdio
  MCP JSON-RPC scaffold matching the newline-delimited framing all four
  ancestor servers speak.
- `tools/profiling/`: the CadGoose soak/profile harness canonized (the two
  repos carried byte-identical copies); target-root seam so neither repo
  profiles the wrong checkout.
- Migrations shipped across eleven consumer repos (app_updates, monitor,
  divoom-control, CadGoose, CadGoose2, sys_updater, routines, necrohand,
  koffee_big, ZeroThunder, ZoneTilerWM): forked length checks deleted,
  coverage/local-CI delegating with step lists diffed old-vs-new, locks and
  ratchets on the shared implementations. Honest keeps documented where a
  local contract was strictly stronger.
- Class fixes found BY the unification: cargo target enumeration via
  find(1) silently lost directory-style test suites (now cargo metadata);
  cpp coverage drove display-taking tests on the user's desktop (now honors
  GOH_CTEST_ARGS); release-kit stanza extraction truncated at ### headings;
  update_dev quit-detection no-op and signature clobbering (PROCESS_NAME,
  verify-then-adhoc).
- `tools/release-kit/`: ONE parameterized releaser (`release.sh` — gate →
  changelog stanza → idempotent annotated tag → push → gh release → Homebrew
  tap bump; every step skippable, every failure names its step, `--dry-run`
  prints without executing), one Apple icon pipeline (`gen_app_icons.py` —
  sips normalize → fixed ten-member ladder → iconutil .icns + optional modern/
  legacy appiconset), and a templated dev installer (`update_dev.sh` — the
  union of the koffee/necrohand/routines copies: quit-before-replace, ditto,
  absolute-path xattr with post-clear verification). Covered by
  `tests/test_release_kit.py` against real git + local bare remotes and a
  stateful fake gh.
- `gates/coverage_gate.sh`: ONE parameterized coverage gate
  (`--lang rust|swift|cpp|py --floor N [--ignore RE] [path]`) replacing six
  per-repo copies that drifted. Rust mode ports the app_updates implementation
  (per-test-target lcov exports + CGU-hash normalization, exact uncovered-line
  reporting); swift/cpp/py adapt necrohand+ZeroThunder, CadGoose2 and
  sys_updater respectively. Floors resolve `--floor` → `GOH_COV_FLOOR_<LANG>`
  → named exit-2 failure; exclusion regexes pass through verbatim, never
  invented. Real cargo llvm-cov e2e test (skipped when the toolchain is absent).
- `gates/local_ci.sh`: ONE declarative step runner for the ~10 copy-pasted
  local-CI orchestrators — steps from `.gatesrc` `GOH_CI_STEPS` (colon-
  separated) and/or `--step` args, tui-styled steps, logs captured to a temp
  dir and dumped on failure, fail accumulator (a failing step never stops the
  run), `--dry-run`, nonzero exit iff any step failed.
- Post-unification adoptions: necrohand/koffee_big/ZeroThunder MCP servers
  ported onto `lib/mcp_scaffold.py` under characterization-pinned wire parity
  (Taxes keeps the official SDK — it provides validation and surface the
  scaffold does not); ZeroThunder's golden suite delegates its pixel math to
  `lib/golden_core.py` (bit-exact parity proven over the baseline corpus);
  ZoneTilerWM's ui sweep likewise (exactly equal changed-pixel counts on all
  70 surfaces) and enabled the swift gate over its lint baseline, fixing a
  pre-existing actor-isolation error for real.

## v0.1.1 _(2026-08-25)_

- License: MIT.
- Policy: permit functional Mac key glyphs (`← ⌘ ⌥ ⌨ ⇧ ⌃ ⏎ ⎋ ↵`) and the text
  operators `⇒ ⇄`; per-repo extras via `GOH_ALLOW` in `.gatesrc`.
- Hygiene: untrack `.opencode` session state; ignore it.

## Gate wiring, harness, and class fixes _(2026-08-24)_

First full audit of the factored-out gate suite found three broken wirings,
two self-violations of the repo's own thesis, and several stated-vs-implemented
drifts. Everything was fixed test-first: the Phase-0 harness was written
against the broken tree and its 10 red failures each mapped to one finding.

**What shipped**:

- `tests/` harness: fixture-repo builder, per-checker contract tests, the
  wiring meta-gate (`tests/test_wiring.py` — parses every gate/hook for
  referenced scripts and asserts they exist), e2e `structural.sh --staged`
  runs, and real-`git commit` hook tests through `core.hooksPath`.
- `checks/check_swift_coverage.py` — shipped; both swift_gate modes called it
  and it never existed. SPM codecov JSON + xcresulttree walk; missing payloads
  are exit 2 with a named reason, never a fake 0%.
- `install.sh` + delegation hooks — hooks resolve the shared checkout via
  `GOH_DIR` (default `~/Projects/gates_of_heck`) so there is exactly one copy
  of every checker; pre-commit runs structural `--staged`; pre-push names the
  missing `tools/gate.sh` instead of exec-failing. This repo is self-hosted.
- `tui/lib.sh` publishes `_lib_*` names via private `_tui_*` impls — the
  styled lib could never engage before (the `_lib_info` probe was always
  false). Naive alias-to-public-name first attempt recursed to a bash
  segfault; caught by the new harness before commit.
- `gates/rust_gate.sh` un-forked onto `_common.sh` (~20 lines); parity proven
  against the preserved legacy fork (`tests/fixtures/rust_gate_legacy.sh`) on
  real crates across six scenarios. Added `goh_step_in`: in-dir steps as pure
  argv, no shell-string interpolation.
- Class fixes: `checks/_gitutil.py` makes ALL staged checks measure the index
  (`git show :path`), not the worktree; `EXCLUDE_PREFIXES` replaced by
  `--exclude` regex from `GOH_EXCLUDE`; `.gatesrc` and vendored tui resolved
  from the git root (subdirectory-safe).
- Stated-vs-implemented: `@generated` window is now the documented "first 40
  lines"; `GOH_SWIFT_COLD` defaults to 1 as claimed and xcode mode honors it
  by wiping the pinned `.build/xcode-dd`; emoji failure message generated from
  the `ALLOWED_ORDERED` constant (was hand-copied, omitting ← ⌘ ⌥ ⌨).

**What deliberately didn't ship**: `cpp_gate.sh` / `kotlin_gate.sh` (no
consumer yet — graduate on third use); CI integration (local-only by design);
disk-check top-5 truncation (documented tradeoff).

**Caught by the gates during this work**: a literal U+21D2 in a test comment
blocked by the freshly installed pre-commit hook (dogfood working as intended);
OS python3 3.9 crashing on `bytes | None` annotations at def time, found by
the self-host test.

**Tests**: 73 new across `tests/`; suite is 70 tests + 6 rust parity tests,
all green; red-proof receipts recorded in test docstrings.
