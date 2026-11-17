#!/usr/bin/env bash
# structural.sh — the gates every repo gets, whatever it is written in.
#
# Nothing here knows about a toolchain. These are the checks that were being
# re-implemented once per repo (eleven copies of the emoji gate, three
# different names for the file-length cap) and drifting apart as they went.
#
#   structural.sh              # whole tree
#   structural.sh --staged     # staged files only (pre-commit scope)
#   structural.sh --full --keep-going   # every step, every red, exit 1 if any (BACKLOG 6.16)
#
# Config, all optional, read from the TARGET repo's .gatesrc:
#   GOH_MAX_LINES=500          # file-length cap; unset disables the check
#   GOH_LINE_EXCLUDE="re1|re2" # paths exempt from the cap
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
# Every step this gate times is the native tier's in-process work, whose raw CPU is its figure
# (lib/step_timings.py RAW_KINDS, BACKLOG 5.3).
export GOH_TIMINGS_KIND=native

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
usage: structural.sh [--staged | --full [--keep-going]]
  no argument   : full-tree checks (same as --full)
  --staged      : staged files only (pre-commit scope, fast)
  --full        : every check (pre-push scope)
  --keep-going  : with --full only — run every step and report every red instead of
                  stopping at the first (BACKLOG 6.16). A refusal still stops.
EOF
    exit 0
fi

# Strict scope argument: "" |--full|--staged are the ONLY accepted forms, plus the one
# flag that qualifies --full. Anything else previously FELL THROUGH to a silent
# full-tree run — a typo (`--stgaed`, `--staged --dry-run`) meant "check everything"
# without ever saying so. Unknown arguments are a usage error naming the accepted
# forms, never a scope guess.
#
# The flags are PARSED, not positional: `--full --keep-going` and `--keep-going --full`
# are the same command, and an order-sensitive parser here would make the second one
# the "unknown argument" case while the first worked — the same silent difference
# between a typo and a real flag that the strictness above exists to remove.
SCOPE=""
KEEP_GOING=0
while [ $# -gt 0 ]; do
    case "$1" in
        --full)
            if [ "$SCOPE" = "--staged" ]; then
                err "structural.sh: --full and --staged together are extra arguments naming"
                err "  opposite scopes. Run one, not both."
                exit 2
            fi
            SCOPE="--full"
            ;;
        --staged)
            if [ "$SCOPE" = "--full" ]; then
                err "structural.sh: --staged and --full together are extra arguments naming"
                err "  opposite scopes. Run one, not both."
                exit 2
            fi
            SCOPE="--staged"
            ;;
        --keep-going) KEEP_GOING=1 ;;
        *)
            err "structural.sh: unknown argument '$1'"
            err "  (accepted: no argument | --full | --staged | --full --keep-going)"
            exit 2
            ;;
    esac
    shift
done

if [ "$KEEP_GOING" = 1 ] && [ "$SCOPE" != "--full" ]; then
    err "structural.sh: --keep-going is --full only: it reports every red in a whole-tree run,"
    err "  and a staged run's steps see the same files (the commit hook's scope). Ask for"
    err "  --full with it, or drop it."
    exit 2
fi

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
# committed" (a private server workspace, 2026-10-08). An older stock hook still passes them, so
# this git drops them itself (contract #12), as does the version read below.
_goh_gate_source_paths="gates checks lib tui"
_goh_dirty_gate_source() {
    (goh_unbind_git; GIT_OPTIONAL_LOCKS=0 git -C "$GOH_ROOT" status --porcelain --untracked-files=normal \
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
    # The version is the TAG (lib/goh_version.sh, GOH.SHIPRELREUSE): HEAD's, read from git, never
    # a number in the working tree -- one session's uncommitted Cargo.toml bump refused every
    # consumer's commit (2026-10-05, tests/test_binary_currency.py), and that file no longer names
    # it. Run from a HEAD export (C4), which has no .git: the commit it pins (`.goh-head`), asked of
    # the live checkout it was cut from (lib/goh_root.sh), as the binary resolver asks. A commit the
    # root cannot read (a tarball; a self-gate's staged-tree commit) has no tag.
    # WANT IS ONE DERIVATION, tagged or not, and only a release is compared with a release
    # (GOH.PROVENTAGHEAD). On a release commit (`X.Y.Z`) the binary must report that release: the
    # stamp names the tag, so the build of that stamp does. Past one (`X.Y.Z-N-g<sha>`), WHICH build
    # serves is the stamp's question (gates/_goh_bin.sh): one build serves every commit with its
    # source, machine-wide (G2e), and says the commit it was built at -- so only a binary claiming
    # ANOTHER release is refused. With no release reachable (`unknown`: a copy of the gates committed
    # afresh, as tests/test_proven.py's fixture is) there is no release to be behind or ahead of: it
    # had read every release build as BEHIND `unknown`, so that suite went red whenever the checkout
    # that built its binary had a tag on HEAD -- a verdict hidden state decided.
    local bin="$1" want got root rev
    root="$(goh_live_root_for "$GOH_ROOT")"
    root="${root:-$GOH_ROOT}"
    rev="$(cat "$GOH_ROOT/.goh-head" 2>/dev/null || true)"
    rev="${rev:-HEAD}"
    want="$(goh_version_at "$root" "$rev")"
    got="$("$bin" --version 2>/dev/null | awk '{print $NF}')"
    if [ -n "$got" ]; then
        case "$want" in
            unknown) return 0 ;;
            *-*) case "$got" in
                     *-* | unknown) return 0 ;;
                     *) [ "$got" = "${want%%-*}" ] && return 0 ;;
                 esac ;;
            *) [ "$got" = "$want" ] && return 0 ;;
        esac
    fi
    # Said as it is: on 2026-10-17 this read "BEHIND" about a binary that was AHEAD (a land had
    # rebuilt bin/goh under a gate pinned to the commit before), and the fix went looking for a
    # missing rebuild. The two releases decide the word.
    local rel="is not the build of" first
    if [ -n "$got" ] && [ "${got%%-*}" != "${want%%-*}" ]; then
        first="$(printf '%s\n%s\n' "${got%%-*}" "${want%%-*}" | sort -V)"
        if [ "${first%%$'\n'*}" = "${got%%-*}" ]; then rel="is BEHIND"; else rel="is AHEAD of"; fi
    fi
    err "the goh binary serving this gate $rel the source: $bin reports ${got:-nothing},"
    err "  $root is $want at ${rev:0:12} (its tag, lib/goh_version.sh). A step added since that build"
    err "  is NOT running, and a step that does not run prints exactly what a step that passes prints."
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

# ── A hook that is an OLDER stock FAILS, with its fix (BACKLOG 6.6). ──────────
# Hooks are COPIED by install.sh, so a stock change reaches no repo until it re-installs (14 repos
# still gated the working tree on 2026-10-05). A retired stock hash is pristine-but-old. It used to
# be a warning, and five repos printed it on every commit for weeks without acting (2026-10-08):
# a gate whose own hook is stale is not running today's gate, so it refuses, before the native
# exec, so both tiers say it (tests/test_hook_currency.py). Only a BYTE-IDENTICAL stale copy (a
# retired stock, or what install.sh recorded writing) is refused: a hook a repo edited is not
# stock, and is its own business. Nothing is rewritten here -- `.githooks` is tracked, and a gate
# that edits the tree it judges dirties the commit it is in. lib/hook_currency.py is the one
# reading, shared with `goh hooks` and the estate audit (GOH.HOOKCURRENCYDUP).
# An entry tagged `same-logic` retired only a comment, so its hook runs today's gate (6.19).
# A retired copy that DELEGATES (execs commit_gate.sh/push_gate.sh) runs today's gate too, by its
# bytes: it is warned, not refused, so a hook-body change strands no consumer (GOH.HOOKSWEEP).
# shellcheck source=gates/_hook_currency.sh
. "$HERE/_hook_currency.sh"  # one reading, shared with goh ship's preflight (GOH.SHIPITEM)
_goh_unread() { err "could not read which hooks are stale (lib/hook_currency.py)"; exit 1; }
_goh_stale="$(goh_stale_hooks)" || _goh_unread
if [ -n "$_goh_stale" ]; then
    while IFS= read -r _goh_h; do
        err "$_goh_h is an OLDER stock hook: the gate it runs is not today's"
    done <<<"$_goh_stale"
    err "  update the hooks, then commit them:  $GOH_ROOT/install.sh \"$PWD\""
    err "  (every estate repo at once: goh hooks --refresh)"
    exit 1
fi
unset _goh_h _goh_stale
# A retired copy that delegates runs today's gate (GOH.HOOKSWEEP): named, never refused.
_goh_aging="$(goh_aging_hooks)" || _goh_unread
if [ -n "$_goh_aging" ]; then
    while IFS= read -r _goh_h; do
        warn "$_goh_h is an older stock hook; it delegates, so today's gate runs -- refresh it"
    done <<<"$_goh_aging"
    warn "  every estate repo at once: goh hooks --refresh  (this repo: $GOH_ROOT/install.sh \"$PWD\")"
fi
unset _goh_h _goh_aging

# ── A consumer's `tools/gate.sh --full` takes the gate slot HERE (lib/gate_slot.sh, v0.39.0). ──
# The consumer copies written before v0.37.0 run this script first and then their own heavy layers,
# and never exec local_ci.sh: hand-run, they raced every push on the machine. install.sh never
# rewrites a gate.sh, so the one goh file they all call carries it: run as a gate.sh's child, this
# takes the slot FOR that gate.sh, held until it ends. --staged (the commit hook) takes none; a
# gate already inside a slot (push gate, local_ci, the starter gate.sh) reuses it.
if [ "$SCOPE" = "--full" ]; then
    # shellcheck source=lib/gate_slot.sh
    . "$GOH_ROOT/lib/gate_slot.sh"
    gate_slot_acquire_for_caller "$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"
fi

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
# THE MARK (BACKLOG 6.7): the commit and push gates read it to tell whether a repo's tools/gate.sh
# ran this layer, and run it themselves when it did not. The scope and the tree, so a structural
# run inside a consumer's fixture does not stand in for the repo's own.
if [ -n "${GOH_STRUCTURAL_MARK:-}" ]; then
    printf '%s\t%s\n' "${SCOPE:---full}" "$(git rev-parse --show-toplevel 2>/dev/null)" >>"$GOH_STRUCTURAL_MARK"
fi
# Arguments were validated above; only the two accepted scopes reach here.
if [ "$SCOPE" = "--staged" ]; then
    goh_bind_hook_index  # the index being committed, when the hook carried one (gates/_git_env.sh)
    exec "$goh_native" structural --staged
fi
# ── AN MCP SERVER IS JUDGED ON THE WIRE (checks/check_mcp_wire.py, queue MCP.G). ──────────────
# The opt-in is the key alone: a repo whose .gatesrc names GOH_MCP_SERVERS gets its servers started,
# spoken to and reaped here, at --full only (a commit never starts a server). Read from the repo's
# own file, never the environment (the environment is not configuration). --keep-going carries the
# red past the native steps; otherwise it fails fast like every step.
_goh_mcp_rc=0
_goh_mcp_top="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
# The gated repo's own config, unknown when this script is linted (GOH.SHX2):
# shellcheck source=/dev/null
if (unset GOH_MCP_SERVERS; cd "$_goh_mcp_top" && [ -f .gatesrc ] && . ./.gatesrc >/dev/null 2>&1 \
        && [ -n "${GOH_MCP_SERVERS:-}" ]); then
    section "MCP servers on the wire (GOH_MCP_SERVERS)"
    "$GOH_PY" "$GOH_ROOT/checks/check_mcp_wire.py" "$_goh_mcp_top" || _goh_mcp_rc=$?
    [ "$_goh_mcp_rc" -eq 0 ] || [ "$KEEP_GOING" = 1 ] || exit "$_goh_mcp_rc"
fi
# `= 1`, never `-n`: KEEP_GOING is 0 or 1, both non-empty, so `-n` sent EVERY --full run down the
# keep-going path and the fail-fast default was never in force (tests/test_check_mcp_wire.py).
# ── A GATE SCRIPT RUNS ON ITS SHARE (checks/check_worker_share.py, GOH.POOLSHARE2). ───────────
# goh's own scripts are held by tests/test_worker_share.py; this holds every repo goh gates: a pool
# sized to the machine in .gatesrc, a Makefile, a *.mk or tools/ is red at the push that adds it.
section "worker share (no gate script sizes a pool to the machine)"
"$GOH_PY" "$GOH_ROOT/checks/check_worker_share.py" "$_goh_mcp_top" || _goh_mcp_rc=$?
[ "$_goh_mcp_rc" -eq 0 ] || [ "$KEEP_GOING" = 1 ] || exit "$_goh_mcp_rc"
if [ "$KEEP_GOING" = 1 ]; then
    "$goh_native" structural --full --keep-going || exit $?
    exit "$_goh_mcp_rc"
fi
exec "$goh_native" structural --full
exit
} # parse-guard
