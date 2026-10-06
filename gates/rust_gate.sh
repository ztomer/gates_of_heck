#!/usr/bin/env bash
# rust_gate.sh — the Rust gates every house Rust project must pass.
#
#   1. cargo fmt --all -- --check
#   2. cargo clippy --workspace --all-targets --all-features -- -D warnings
#   2b. gates/rust_manifest_gate.sh — CARGO's own lints, which step 2 cannot
#      see: `-D warnings` sets rustc lint levels, cargo's namespace is
#      separate, so cargo warns and exits 0. Reads output, not exit code.
#   2c. EVERY SHIPPED CONFIGURATION, from GOH_RUST_LINT_CONFIGS. Step 2 lints
#      one cfg: this machine's target, with all features on. A crate that is
#      part `cfg(target_os = ...)` or part `cfg(feature = ...)` has halves that
#      command never compiles, so it cannot report on them. Unset keeps step 2
#      alone, with a printed nudge.
#   2d. goh lints (gates/goh.sh) — a workspace's [workspace.lints] is a
#      DECLARATION; a member applies it with `[lints] workspace = true`. A crate
#      that never says so inherits nothing and looks clean.
#   2e. cargo machete — unused dependencies. `[lints.cargo]
#      unused_dependencies = "deny"` looks like this and is not: the key needs
#      -Zcargo-lints on nightly, so on stable cargo prints "unused manifest key"
#      and exits 0. A missing cargo-machete FAILS the gate up front (goh_require).
#   3. goh no-allow (gates/goh.sh) — no #[allow] and no #[expect]; fix findings, never silence.
#      The HOUSE checker, always. Until 2026-09-14 this step looked for a
#      repo-local tools/check_no_allow.py and skipped when absent, so four
#      repos carried vendored copies and the rest were not checked at all.
#   3a. goh empty-assert (gates/goh.sh) — no `assert!(x.is_empty())` and no
#      `x.len() == 0` inside assert!. Ahead of clippy on purpose, and it is not
#      a duplicate of the lint: clippy reports NOTHING for an assert that
#      carries a message, which is the shape most people write on purpose. That
#      gap was measured, not assumed (2026-10-02): clippy was green on this very
#      repo while 5 instances sat in the tree, two commits after a sweep for the
#      same lint. Runs first because it reads a few dozen files and reports the
#      offending lines, where clippy needs the crate to compile and reports at
#      the end -- so the price of a violation is the violation, not the repo.
#   4. coverage floor, when stated (GOH_COV_FLOOR_RUST or GOH_COV_FLOORS_JSON
#      in .gatesrc) — via gates/coverage_gate.sh, with the floor passed as
#      explicit argv (.gatesrc values are shell variables, NOT exported, so
#      the callee cannot see them through the environment). Unset keeps
#      fmt+clippy only, with a printed nudge (the py/swift-gate precedent).
#      GOH_RUST_COVERAGE=defer (env) skips it BY NAME for a commit gate whose
#      push gate runs it; any other value fails.
#
#   rust_gate.sh                       # run from repo root (uses $PWD)
#   rust_gate.sh <repo>                # run from anywhere
#   rust_gate.sh <repo> <cargo_dir>    # cargo lives in a subdir (e.g. divoomd/)
#
# When <cargo_dir> is given, fmt/clippy run there while the no-#[allow]
# checker still scans from the repo root.
#
# NOTE: sccache is expected via RUSTC_WRAPPER (see ~/.zshenv). fmt and clippy
# are largely cache-hostile; the real cache win is on build/test steps.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# --each-crate: every crate of the repo, concurrently, repo-wide scans once (BACKLOG P2).
[ "${1:-}" = --each-crate ] && { shift; exec bash "$HERE/rust_each_crate.sh" "$@"; }
. "$HERE/_common.sh"

# GOH_RUST_GROUPS (env, read BEFORE .gatesrc so a repo file cannot narrow its own gate): which of
# the three groups below this run proves. rust_each_crate.sh runs `repo` once and `crate,coverage`
# per crate, which is the whole point of it. Default: all three. An unknown word is a miswiring.
_groups=",${GOH_RUST_GROUPS:-crate,repo,coverage},"
_g="${_groups#,}"
while [ -n "${_g%,}" ]; do
    case "${_g%%,*}" in crate|repo|coverage) ;; *)
        die "GOH_RUST_GROUPS='${GOH_RUST_GROUPS}' - the groups are crate, repo and coverage" ;; esac
    _g="${_g#*,}"
done
_group_on() { case "$_groups" in *",$1,"*) return 0 ;; esac; return 1; }

repo="${1:-$PWD}"
cargo_dir="${2:-$repo}"
cd "$repo"
cargo_dir="$(cd "$cargo_dir" && pwd)"

# A fixture's environment must not decide its verdict -- and `goh_step`, which every step below
# runs through, now reads GOH_STEP_TIMEOUT for its ceiling, so a key that merely sat in the
# environment would silently re-arm (or remove) the bound on every step of every fixture.
# structural.sh already
# drops these before sourcing .gatesrc; rust_gate.sh is spawned by every test
# that exercises it, with GOH_DIR at a real checkout, so the same class of
# leak would let an inherited GOH_COV_FLOOR_RUST arm a coverage floor no
# fixture declared. A key is configuration iff the repo's own file says so.
unset GOH_COV_FLOOR_RUST GOH_RUST_LINT_CONFIGS GOH_RUST_LINT_CARGO GOH_DEPS_RATCHET \
      GOH_CROSS_REPO_ROOT GOH_PYTHON_FORMATTED GOH_SKILLS_CORPUS \
      GOH_STEP_TIMEOUT GOH_STEP_GRACE GOH_LCI_TIMEOUT 2>/dev/null || true

[ -f .gatesrc ] && . ./.gatesrc

goh_init "rust"
goh_tree_stamp

command -v cargo >/dev/null 2>&1 || die "cargo not on PATH"
goh_require cargo-machete "cargo install cargo-machete"

# THE LOCKFILE IS AN INPUT, NEVER AN OUTPUT. Without `--locked`, cargo re-resolves and REWRITES a
# lockfile its manifests disagree with, then lints and tests happily: a manifest-only dependency
# change passes green and the lockfile diff is the only trace of what moved (antiknob, 2026-10-04,
# which carried tools/lock_guard.sh because this gate had no `--locked` anywhere). Pinned from both
# ends: every resolving cargo call below passes `--locked` (tests/test_rust_gate.py reads the source
# for one that does not), and the lock is hashed here and re-checked at the end, so no step --
# including the ones that take no such flag -- can leave a rewritten lockfile behind.
manifest="$(cd "$cargo_dir" && cargo locate-project --workspace --message-format plain 2>/dev/null)" \
    || die "[rust] cargo cannot find a workspace manifest from $cargo_dir"
lockfile="$(dirname "$manifest")/Cargo.lock"
[ -f "$lockfile" ] || die "[rust] no Cargo.lock at $lockfile -- commit one; without it the gate certifies whatever resolves today"
lock_sum="$(shasum -a 256 "$lockfile")"
# ── THE THREE GROUPS, each proven once on its own inputs (BACKLOG P3, gates/_rust_proven.sh) ──
# crate: what reads this cargo workspace and the packages it reaches by path -- keyed on exactly
#   those (`goh rust-scope`), and recorded only if the compiler's dep-info read nothing else.
# repo: the three scans that read the WHOLE repo -- keyed on the whole tree, so in a 29-crate push
#   the first crate records them and the other 28 find the record.
# coverage: the instrumented run, keyed like `crate` and separately, so a pre-commit run (coverage
#   deferred) still proves the crate group for the push that follows.
rust_crate_checks() {
    goh_step_in "$cargo_dir" "the lockfile resolves --locked" cargo metadata --locked --format-version 1

    goh_step_in "$cargo_dir" "fmt" cargo fmt --all -- --check

    goh_step_in "$cargo_dir" "clippy (-D warnings, all targets, all features)" \
        cargo clippy --locked --workspace --all-targets --all-features -- -D warnings

    # EVERY SHIPPED CONFIGURATION, NOT JUST THIS ONE.
    #
    # `cargo clippy` reports on the cfg it compiled for and is silent about every
    # other. A crate that is half `cfg(target_os = "macos")` is checked by a Mac and
    # by an ubuntu runner, each seeing its own half, and NEITHER failing on the
    # other's code -- so between them the halves look covered while nothing compares
    # them. Measured on `monitor`, 2026-09-07: turning the workspace lints on for
    # one crate found 26 findings on the host and 48 for linux-musl, ten of which
    # were in src the host cannot see, plus two `#[expect]`s that were UNFULFILLED
    # there -- and an unfulfilled expectation under `-D warnings` is an error, so
    # the build was broken for the platform that ships while every gate was green.
    #
    # Each entry is extra argv for clippy, ':'-separated, e.g.
    #   GOH_RUST_LINT_CONFIGS='--target x86_64-unknown-linux-musl -p agent:-p d --no-default-features'
    #
    # THE CARGO THAT CROSS-LINTS (BACKLOG P1g). A `--target` config builds the target's build
    # scripts, and those compile C FOR THE TARGET: ring needs a musl C compiler, which plain
    # `cargo clippy` on macOS has not got, so media_server ran its cross clippy serially, in its
    # own script, 46 s after the crate fan-out ended. GOH_RUST_LINT_CARGO names the cargo command
    # for `--target` configs (`cargo-zigbuild`, which brings `zig cc`); host configs keep `cargo`.
    # Required up front, like every tool this gate cannot run without.
    if [ -n "${GOH_RUST_LINT_CONFIGS:-}" ]; then
        lint_cargo="${GOH_RUST_LINT_CARGO:-cargo}"
        case "$lint_cargo" in
            cargo) ;;
            cargo-zigbuild)
                goh_require cargo-zigbuild "cargo install --locked cargo-zigbuild"
                goh_require zig "brew install zig (cargo-zigbuild drives zig cc)" ;;
            *) command -v "$lint_cargo" >/dev/null 2>&1 \
                   || die "[rust] GOH_RUST_LINT_CARGO='$lint_cargo' is not on PATH" ;;
        esac
        installed="$(rustup target list --installed 2>/dev/null || true)"
        saved_ifs="$IFS"; IFS=':'
        for cfg in $GOH_RUST_LINT_CONFIGS; do
            IFS="$saved_ifs"
            [ -n "$cfg" ] || continue
            # A missing target std would make clippy inspect NOTHING while exiting
            # non-zero for an unrelated reason. Name the fix instead.
            case "$cfg" in
                *--target*)
                    tgt="$(printf '%s\n' "$cfg" | sed -n 's/.*--target[= ]\([^ ]*\).*/\1/p')"
                    if [ -n "$tgt" ] && ! printf '%s\n' "$installed" | grep -qx "$tgt"; then
                        err "[rust] target $tgt is not installed; this step would inspect nothing."
                        err "  rustup target add $tgt"
                        exit 1
                    fi
                    ;;
            esac
            runner=cargo via=""
            case "$cfg" in *--target*) runner="$lint_cargo" ;; esac
            [ "$runner" = cargo ] || via=" via $runner"
            # shellcheck disable=SC2086
            goh_step_in "$cargo_dir" "clippy ($cfg)$via" \
                "$runner" clippy --locked --all-targets $cfg -- -D warnings
            IFS=':'
        done
        IFS="$saved_ifs"
    else
        warn "only this machine's cfg was linted — set GOH_RUST_LINT_CONFIGS in .gatesrc"
        warn "  for every target and feature set the crate actually ships to"
    fi

    # UNUSED DEPENDENCIES. `[lints.cargo] unused_dependencies = "deny"` looks like
    # this gate and is not one: the key needs `-Zcargo-lints` on nightly, so on
    # stable cargo emits "unused manifest key `lints.cargo`" and exits 0. It had
    # never been enforced on any toolchain in the repo that declared it (routines,
    # removed in 12edbc2). cargo-machete does the job on stable.
    #
    # It is a hard failure, not a warning, because it exits 1 on findings and a
    # finding is either real debt or a false positive worth recording with its
    # reason -- both are actions, neither is "look at this every commit".
    #
    # Absent, the gate FAILS up front (goh_require above): a missing tool must never count toward a pass.
    # Its own blind spot is ident-based scanning, so a crate whose lib name differs
    # from its package name (md-5 -> md5) or that is reached only through a string
    # (`#[serde(with = "serde_bytes")]`) reads as unused. Those go in the crate's
    # own `[package.metadata.cargo-machete] ignored = [...]` WITH the reason --
    # policy central, exemptions local.
    goh_step_in "$cargo_dir" "no unused dependencies" cargo machete

    # Cargo's own lint namespace. Separate from clippy because clippy CANNOT fail
    # on these -- see the header of the script for the measurement that proved it.
    goh_step_in "$cargo_dir" "cargo lints (manifest)" \
        bash "$HERE/rust_manifest_gate.sh" "$cargo_dir"

    # Dependency currency. Split severity on purpose:
    #   * FATAL: a direct dependency pinned BELOW what the graph already resolves.
    #     Offline, deterministic, and a bug by house rule -- our pin is why two
    #     majors of one crate are in the tree, and the compiler reports that as a
    #     type error naming the TYPE, never the pin.
    #   * REPORTED, not failed: anything behind crates.io. A major moves in its own
    #     commit and a patch is routine, so a gate that failed on drift would be red
    #     on every honest commit -- and a permanently-red gate is one nobody reads.
    #     GOH_DEPS_STRICT=1 opts a repo into failing on majors; GOH_DEPS_RATCHET
    #     freezes the majors it has already triaged so they cannot grow back.
    # GOH_DEPS_OFFLINE=1 skips the network arm AND says so, rather than printing a
    # clean bill it did not earn.
    # A STRING, not an array: macOS ships bash 3.2, where an EMPTY array expands
    # to an unbound variable under `set -u`, and this gate runs with `set -euo
    # pipefail`. The estate's own tests caught it on the first run, which is what
    # they are for.
    _deps_args=""
    [ "${GOH_DEPS_STRICT:-}" = "1" ] && _deps_args="--strict"
    [ -n "${GOH_DEPS_RATCHET:-}" ] && _deps_args="$_deps_args --ratchet $GOH_DEPS_RATCHET"
    [ "${GOH_DEPS_OFFLINE:-}" = "1" ] && _deps_args="$_deps_args --offline"
    # shellcheck disable=SC2086 # deliberate word-splitting: this IS the argv list
    goh_step "dependency currency" bash "$HERE/goh.sh" deps $_deps_args
}

rust_repo_checks() {
    # Native first: `goh lints` carries this step (parity-pinned by
    # tests/test_goh_lints_parity.py), through gates/goh.sh — the one resolver
    # every caller shares, with the Python checker as its stated fallback.
    goh_step "lint policy is inherited" bash "$HERE/goh.sh" lints

    # GOH_EXCLUDE (regex, from .gatesrc) exempts a vendored tree here as it does
    # in the structural checks: third-party code is not ours to re-lint.
    # Native first: `goh no-allow` carries this step (parity-pinned by
    # tests/test_goh_noallow_parity.py), through gates/goh.sh like the lints step.
    goh_step "no #[allow] / #[expect]" bash "$HERE/goh.sh" no-allow ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}

    # Same GOH_EXCLUDE exemption: a vendored tree is not ours to re-lint.
    goh_step "no emptiness asserts" bash "$HERE/goh.sh" empty-assert ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
}

rust_coverage_checks() {
    if [ -n "${GOH_COV_FLOOR_RUST:-}" ]; then
        goh_step "coverage (floor ${GOH_COV_FLOOR_RUST}%)" \
            bash "$HERE/coverage_gate.sh" --lang rust \
            --floor "$GOH_COV_FLOOR_RUST" "$cargo_dir"
    else
        goh_step "coverage (per-target floors)" \
            bash "$HERE/coverage_gate.sh" --lang rust \
            --floors-json "$GOH_COV_FLOORS_JSON" "$cargo_dir"
    fi
}

# shellcheck source=gates/_rust_proven.sh
. "$HERE/_rust_proven.sh"
_group_on crate && rust_proven_group crate crate rust_crate_checks
_group_on repo && rust_proven_group repo tree rust_repo_checks

# GOH_RUST_COVERAGE=defer: the caller's PUSH gate checks the floor, so a
# commit gate need not rebuild every touched crate instrumented (a release
# touches them all). Named, never silent; any other value is a miswiring.
case "${GOH_RUST_COVERAGE:-}" in
    ""|defer) ;;
    *) die "GOH_RUST_COVERAGE='${GOH_RUST_COVERAGE}' - the only value is 'defer'" ;;
esac

if ! _group_on coverage; then
    :   # not this run's group: rust_each_crate.sh asked for the others (GOH_RUST_GROUPS)
elif [ "${GOH_RUST_COVERAGE:-}" = defer ]; then
    info "coverage deferred to the push gate (GOH_RUST_COVERAGE=defer)"
elif [ -n "${GOH_COV_FLOOR_RUST:-}" ] || [ -n "${GOH_COV_FLOORS_JSON:-}" ]; then
    rust_proven_group coverage crate rust_coverage_checks
else
    warn "no coverage floor — set GOH_COV_FLOOR_RUST in .gatesrc"
fi

[ "$(shasum -a 256 "$lockfile")" = "$lock_sum" ] \
    || die "[rust] a step rewrote $lockfile -- the gate must judge the lockfile, never repair it"
ok "Cargo.lock is byte-identical to the one the gate started from"

goh_done
exit
} # parse-guard
