#!/usr/bin/env bash
# proven.sh — run one gate step, unless that exact step already passed on this exact tree.
#
# THE COST. One commit on ~/Projects/monitor ran its expensive suite THREE times back to back
# (measured 2026-09-23): the repo's pre-commit hook ran the checkers, clippy, the e2e suites and
# `bash tools/coverage_check.sh` -- the whole test suite under llvm-cov, ~10 min under load; then
# pre-push ran `tools/gate.sh --full`, which runs every GOH_CI_STEPS step -- the same coverage
# step among them -- over the same tree again; then GitHub CI ran it a third time. The second
# run could learn nothing the first had not: same bytes, same command, same toolchain.
#
# THE FIX. A step that exits 0 leaves a RECORD keyed by sha256 of three things: the tree (below),
# the exact step string, and the identity of everything else that decides its verdict -- the
# gates_of_heck checkout's HEAD, its diff and its untracked files (work in progress on the gates
# invalidates what they proved), the toolchain versions for the languages the repo tracks, a few
# environment variables (CI, RUSTFLAGS, ... plus the repo's GOH_PROVEN_ENV), and the content of
# the ignored files named in GOH_EXPORT_KEEP. The same key within the TTL (GOH_PROVEN_TTL_S,
# default 24 h) prints one line saying who proved it and how long ago, and skips. CI stays the
# independent check: it never sees these records.
#
# WHY IT STAYS HONEST: THE CLEAN-TREE PRECONDITION. The key's tree is `git write-tree` -- the
# index -- and there is a key ONLY when the working tree equals the index: no unstaged edit, no
# untracked file that is not ignored. So a record always names bytes git can name, the same
# bytes the step read; after the commit lands HEAD^{tree} is that tree, and the push-time run in
# the checkout (or in push_gate.sh's worktree, which shares the git dir where records live)
# computes the same key. A dirty tree has no key: the step runs, records nothing, skips nothing.
# The key is computed again after the step; if the tree moved while it ran, nothing is recorded.
# A failing step never records, and a record is never consulted for a different step string.
#
# THE ENVIRONMENT. A proven step must run where local_ci.sh would run it, or a pass under one
# environment would be trusted under another. So this CLI sources the repo's .gatesrc first,
# exactly as local_ci.sh does, and the step inherits what it exports.
#
# Usage: proven.sh [--label NAME] [--log FILE] [--] '<step string>'
#   The step string runs under `bash -c` from the current directory, as local_ci.sh runs it --
#   pass it VERBATIM as GOH_CI_STEPS spells it, or the push-time run keys a different step.
#   --label  who is recording (shown by a later skip); default "proven.sh"
#   --log    send the step's output to FILE; this script's own lines stay on stdout
# Exit: the step's code; 0 when skipped; 2 on a usage or configuration error.
# GOH_PROVEN=0 disables the cache (always run, never record).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tui/lib.sh
. "$HERE/../tui/lib.sh"
# shellcheck source=gates/_proven.sh
. "$HERE/_proven.sh"

label="proven.sh"
log=""
while [ $# -gt 0 ]; do
    case "$1" in
        --label) [ $# -ge 2 ] || die "proven: --label needs a name" 2; label="$2"; shift 2 ;;
        --log)   [ $# -ge 2 ] || die "proven: --log needs a file" 2; log="$2"; shift 2 ;;
        -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        --) shift; break ;;
        -*) die "proven: unknown option: $1" 2 ;;
        *) break ;;
    esac
done
[ $# -eq 1 ] || die "proven: expected exactly one step string (got $#) -- quote it" 2
step_cmd="$1"
[ -n "$step_cmd" ] || die "proven: the step string is empty" 2

top="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "$top" ] && [ -f "$top/.gatesrc" ]; then
    # shellcheck source=/dev/null
    . "$top/.gatesrc"
fi
proven_settings || exit 2

run_it() {
    if [ -n "$log" ]; then
        bash -c "$step_cmd" >"$log" 2>&1 </dev/null
    else
        bash -c "$step_cmd"
    fi
}

key="" tree=""
if pair="$(proven_key "$step_cmd" </dev/null)"; then
    read -r key tree <<<"$pair"
    if hit="$(proven_lookup "$key" "$step_cmd")"; then
        read -r age by <<<"$hit"
        [ -z "$log" ] || : >"$log"
        step "$label: proven on this tree $age ago by $by — skipped"
        exit 0
    fi
fi

set +e
run_it
rc=$?
set -e
if [ "$rc" -eq 0 ] && [ -n "$key" ]; then
    after="$(proven_key "$step_cmd" </dev/null || true)"
    if [ "$after" = "$key $tree" ]; then
        proven_record "$key" "$tree" "$step_cmd" "$label" || warn "proven: could not write the record"
    else
        step "$label: the tree or the gates moved while the step ran — not recorded as proven"
    fi
fi
exit "$rc"
