#!/usr/bin/env bash
# structural.sh — the gates every repo gets, whatever it is written in.
#
# Nothing here knows about a toolchain. These are the checks that were being
# re-implemented once per repo (eleven copies of the emoji gate, three
# different names for the file-length cap) and drifting apart as they went.
#
#   structural.sh              # whole tree
#   structural.sh --staged     # staged files only (pre-commit scope)
#
# Config, all optional, read from the TARGET repo's .gatesrc:
#   GOH_MAX_LINES=500          # file-length cap; unset disables the check
#   GOH_LINE_EXCLUDE="re1|re2" # paths exempt from the cap
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
usage: structural.sh [--staged | --full]
  no argument : full-tree checks (same as --full)
  --staged    : staged files only (pre-commit scope, fast)
  --full      : every check (pre-push scope)
EOF
    exit 0
fi
SCOPE="${1:-}"

# Strict scope argument: "" |--full|--staged are the ONLY accepted forms.
# Anything else previously FELL THROUGH to a silent full-tree run — a typo
# (`--stgaed`, `--staged --dry-run`) meant "check everything" without ever
# saying so. Unknown arguments are a usage error naming the accepted forms,
# never a scope guess.
case "$SCOPE" in
    ""|--full|--staged) ;;
    *)
        err "structural.sh: unknown argument '$SCOPE' (accepted: no argument | --full | --staged)"
        exit 2
        ;;
esac
[ $# -le 1 ] || {
    err "structural.sh: unexpected extra arguments: $* (accepted: no argument | --full | --staged)"
    exit 2
}

# ── The gate runs SOURCE, and uncommitted source is a different gate. ───────
#
# Every step below is a Python file under $GOH_DIR/checks, and this script is
# itself a file under $GOH_DIR/gates. Both are read from the shared checkout's
# WORKING TREE, at the moment the gate runs, in whatever repo it is running for.
# Appending one comment line to one of them changes the verdict of every repo
# whose hooks delegate here — with no reinstall, no output and no refusal, and
# that combination is the worst property a gate can have: silent, and total.
#
# This WARNS and runs, at both scopes, and that is deliberate — the refusal is in
# scripts/build-goh.sh, and here is the measurement that put it there. Failing
# here instead looked right and was wrong for two reasons, both found by doing it:
#   - this script is what 30 repos' PRE-COMMIT hooks run. A gate that failed on
#     uncommitted source would stop every repo in the estate committing, over
#     work in a checkout none of them can see or fix.
#   - every test in the estate that spawns this gate with GOH_DIR pointing at a
#     real checkout would go red the moment a session had uncommitted work here.
#     Measured: 22 tests, from one session's own box halfway through. A gate whose
#     verdict depends on the working tree is the same defect as the one two dozen
#     lines below used to have.
# So the shape is: PUBLISH refuses (scripts/build-goh.sh, which install.sh calls,
# so the "no reinstall" half is closed where a reinstall actually happens) and
# CERTIFY names itself — here, in every repo's pre-commit, and again in
# gates/push_gate.sh where a human reads the scrollback. What the gate can no
# longer do is change silently.
#
# WHAT COUNTS AS DIRTY: anything `git status --porcelain` reports under those
# paths — modified, staged, deleted, renamed, or untracked-and-not-ignored.
# Untracked counts because a checker that is written but not yet wired is still
# gate source, and the conservative direction is the one that refuses. Ignored
# build output (`__pycache__/`, `*.pyc`) is excluded by git itself, so running a
# checker never makes the tree dirty and warns about itself.
#
# THE PATHS are what a gate READS OR EXECUTES at run time. `crates/` is
# deliberately in build-goh.sh's list and not in this one: uncommitted Rust
# changes nothing until someone rebuilds bin/goh, which is a publish, and a
# publish is where refusing costs nothing. `install.sh`, `hooks/` and `tools/`
# are read at INSTALL time, not by a gate run. A tree that is not a git checkout
# — a tarball install — has nothing to compare and says nothing, like every other
# gate here.
#
# THE GATES CHECKOUT IS ANOTHER REPOSITORY. A hook's GIT_DIR/GIT_INDEX_FILE, inherited, made this
# `git status` answer with the CONSUMER's index against the export's files: every commit from a
# linked worktree listed the consumer's tree as deleted and the gates as untracked, under "NOT
# committed" (media_server, 2026-10-08). An older stock hook still passes them, so this git drops
# them itself (contract #12), as does the version read below.
_goh_gate_source_paths="gates checks lib tui"
_goh_dirty_gate_source() {
    (goh_unbind_git; git -C "$GOH_ROOT" status --porcelain --untracked-files=normal \
        -- $_goh_gate_source_paths 2>/dev/null) || true
}

# A STEP THAT DOES NOT RUN PRINTS EXACTLY WHAT A STEP THAT PASSES PRINTS.
#
# `goh` carries the pipeline, so a binary built before a step was added is a
# pipeline that does not contain it — and every signal the run emits is
# indistinguishable from a clean run. Measured 2026-10-02: a `bin/goh`
# predating the markdown-links step, so that step did not run, and two
# long-standing broken links sat in the tree the whole time (one a TOC entry
# contradicting the heading it linked to).
#
# `scripts/build-goh.sh` already compares these two numbers — but only INSIDE
# the build, which is why the gap was invisible: nothing between a version bump
# and the next `install.sh` could see it, and 30 repos' hooks read the binary in
# between. This is that comparison, at gate time, where a run of the gate can
# see it.
#
# FAIL-CLOSED, and deliberately so: the alternative is the status quo. The one
# soft case is a checkout with no manifest to compare against (a tarball
# install), which is reported by name rather than obeyed silently — the
# swiftlint and cargo-machete precedent, and the same rule check_empty_scope.py
# holds every gate to.
goh_require_current() {
    local bin="$1" manifest="$GOH_ROOT/Cargo.toml" want got
    if [ ! -f "$manifest" ]; then
        warn "no $manifest — cannot tell whether $bin is current, and it may be skipping steps"
        return 0
    fi
    # HEAD's version, never the working tree's: one session's uncommitted bump refused every
    # consumer's commit (2026-10-05, tests/test_binary_currency.py). EXCEPT under GOH_LIVE=1, where
    # the run's subject IS the working tree -- its gates, and its binary (goh_live_binary, or one
    # named by GOH_BIN): an uncommitted version bump read as "behind" there and refused the bump's
    # own verification (the v0.23.0 release, 2026-10-06). Consumers never run GOH_LIVE.
    local where="at HEAD"
    if [ -n "${GOH_LIVE:-}" ]; then
        where="in the working tree (GOH_LIVE)"
        want="$(grep -m1 '^version = ' "$manifest" | cut -d'"' -f2)"
    else
        # Read whole, then searched: `producer | grep -m1` under pipefail is a race (goh
        # early-exit-pipe) -- grep's early exit SIGPIPEs the producer and fails the pipeline.
        local text
        text="$( (goh_unbind_git; git -C "$GOH_ROOT" show HEAD:Cargo.toml) 2>/dev/null \
            || cat "$manifest")"
        want="$(grep -m1 '^version = ' <<<"$text" | cut -d'"' -f2)"
    fi
    got="$("$bin" --version 2>/dev/null | awk '{print $NF}')"
    if [ -n "$want" ] && [ "$got" = "$want" ]; then
        return 0
    fi
    err "the goh binary serving this gate is BEHIND the source: $bin reports ${got:-nothing},"
    err "  $manifest declares $want $where. A step added since that build is NOT running, and"
    err "  a step that does not run prints exactly what a step that passes prints."
    err "  Rebuild it:  $GOH_ROOT/scripts/build-goh.sh    (or ./install.sh, which calls it)"
    return 1
}

# A STEP THE TREE DECLARES AND THE BINARY LACKS IS NAMED, never silently absent (BACKLOG 1.4).
#
# The version and the source stamp both compare HEAD with the binary, and both pass while the tree
# being judged declares a step HEAD has not got: on 2026-10-09 a staged run printed "all structural
# gates passed" in 0.3 s over a tree whose new step had never run. The binary embeds the step
# manifest it was built from (`goh structural --list-steps`); the manifest read here is the judged
# tree's own when it is gates_of_heck, else the export's. A missing step WARNS: a commit that adds a
# step is judged by HEAD's binary by design (C4), and refusing it would stop every step ever being
# added -- so it is said, by name, with when it will first run.
goh_name_missing_steps() {
    local bin="$1" top manifest carried missing
    top="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
    manifest="$top/crates/goh/structural_steps.txt"
    [ -f "$manifest" ] || manifest="$GOH_ROOT/crates/goh/structural_steps.txt"
    [ -f "$manifest" ] || return 0
    if ! carried="$("$bin" structural --list-steps 2>/dev/null)"; then
        warn "$bin cannot list its steps (it predates goh structural --list-steps): which of"
        warn "  $manifest it carries cannot be read"
        return 0
    fi
    missing="$(grep -v -e '^#' -e '^[[:space:]]*$' "$manifest" \
        | grep -vxF -f <(printf '%s\n' "$carried") || true)"
    [ -n "$missing" ] || return 0
    warn "these steps are declared in $manifest but NOT carried by $bin,"
    warn "  so they have not run on this change (they run once a binary built with them judges):"
    printf '%s\n' "$missing" | sed 's/^/    /' >&2
}

# ── The native binary is the only tier (Phase N3). ───────────────────────────
# `goh structural` carries this whole pipeline. It was proven step for step against the Python
# pipeline that used to follow this line, and that tier is retired: two implementations of one
# gate drift, and the second had become a way to run a weaker gate without saying so. Without a
# binary this REFUSES -- a gate that falls back to something else is a different gate.
_goh_dirty="$(_goh_dirty_gate_source)"
if [ -n "$_goh_dirty" ]; then
    # Reached only with GOH_LIVE=1 (or no git to export from): consumers run HEAD's export (C4).
    warn "the gates about to judge this tree are NOT committed — a verdict from uncommitted"
    warn "  gate source is not reproducible from any commit. GOH_LIVE=1 runs these files:"
    printf '%s\n' "$_goh_dirty" | sed 's/^/    /' >&2
    warn "  … in the gates checkout at $GOH_ROOT, not in this repo. Without GOH_LIVE every"
    warn "  gate runs that checkout's HEAD (gates/_from_head.sh), so these edits judge only you."
    unset _goh_dirty
fi

# ── A hook that is an OLDER stock is named, with its fix. ─────────────────────
# Hooks are COPIED by install.sh, so a stock change reaches no repo until it re-installs (14 repos
# still gated the working tree on 2026-10-05). A retired stock hash is pristine-but-old; named
# here, before the native exec, so both tiers say it (tests/test_hook_currency.py).
_goh_hooks="$(git config core.hooksPath 2>/dev/null || echo .githooks)"
for _goh_h in pre-commit pre-push; do
    [ -f "$_goh_hooks/$_goh_h" ] || continue
    _goh_sum="$(shasum -a 256 <"$_goh_hooks/$_goh_h" | cut -d' ' -f1)"
    if grep -q "^$_goh_sum  $_goh_h" "$GOH_ROOT/retired_hooks.sha256" 2>/dev/null; then
        warn "$_goh_hooks/$_goh_h is an OLDER stock hook; update it:  $GOH_ROOT/install.sh \"$PWD\""
    fi
done
unset _goh_hooks _goh_h _goh_sum

# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"
goh_resolve_native
if [ -z "$goh_native" ]; then
    err "structural: no native goh binary -- ${goh_native_why:-it is not built}."
    err "  The Python tier is retired (Phase N3): the binary is the only tier, so this refuses"
    err "  rather than run something else. Build it: $GOH_ROOT/scripts/build-goh.sh (needs cargo)."
    exit 1
fi
goh_require_current "$goh_native" || exit 1
goh_name_missing_steps "$goh_native"
# Arguments were validated above; only the two accepted scopes reach here.
if [ "$SCOPE" = "--staged" ]; then
    goh_bind_hook_index  # the index being committed, when the hook carried one (gates/_git_env.sh)
    exec "$goh_native" structural --staged
fi
exec "$goh_native" structural --full
exit
} # parse-guard
