#!/usr/bin/env bash
# rust_each_crate.sh — the house Rust gate over EVERY crate of a repo, side by side (BACKLOG P2).
#
#   rust_gate.sh --each-crate [repo]
#   rust_gate.sh --each-crate --list [repo]   # the crates it would gate, one repo-relative
#                                             # directory per line, biggest first; runs nothing
#
# For a repo of many independent cargo workspaces (media_server: 29 crates under crates/), which
# used to hand-roll this with `xargs -P 4`, a log dir and a `*.failed` sweep. Here it is one
# scheduler, the one every repo's CI steps already use (local_ci.sh): its job budget, its
# declared-order reports, its fail accumulator, its ceilings and its orphan canary.
#
# * THE CRATES: every tracked Cargo.toml not inside another one's directory (a crate's testkit or
#   fuzz manifest is its own crate's business), minus GOH_EXCLUDE. BIGGEST FIRST, by tracked .rs
#   files: the slowest crate (mediaops, 168 s of a 199 s layer) used to start last and set the finish.
#   GOH_EXCLUDE means what it means to every other check: `re.search` over a repo-relative FILE
#   path -- here the manifest's, so `'^crates/vendored-rs/'` takes that crate out. It was matched
#   against the bare directory, where a trailing-slash pattern never matched and the crate it named
#   was gated anyway; and by `grep -E`, which refused a look-ahead and called it "no Cargo.toml".
# * --list prints the selection and exits: a caller that must know which crates this gates (a
#   repo's own layer that skips them) asks, rather than keeping a copy of the rule to drift. The
#   run and the list are the same `ordered` below, so they cannot disagree.
# * THE REPO-WIDE SCANS ONCE (BACKLOG P1f): `lints`, `no-allow` and `empty-assert` read the whole
#   repo whichever crate calls them -- 29 crates started together all missed the shared record at
#   once and ran them 29 times. So they are one step of their own (GOH_RUST_GROUPS=repo) and each
#   crate runs only its own groups (GOH_RUST_GROUPS=crate,coverage).
# * GOH_RUST_JOBS (.gatesrc, default 4) crates at once.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

list=0
[ "${1:-}" = --list ] && { list=1; shift; }
case "${1:-}" in -*) die "rust_gate.sh --each-crate [--list] [repo]: unknown option '$1'" ;; esac
repo="${1:-$PWD}"
cd "$repo"
repo="$(pwd)"
unset GOH_RUST_JOBS GOH_EXCLUDE 2>/dev/null || true
[ -f .gatesrc ] && . ./.gatesrc

case "${GOH_RUST_JOBS:-4}" in
    *[!0-9]*|0|"") die "GOH_RUST_JOBS must be a positive integer of crates at once (got '${GOH_RUST_JOBS}')" ;;
esac
# A step string is a shell string, and local_ci splits its step list on colons.
case "$HERE" in *:*) die "the gates path holds a colon, which local_ci.sh reads as a step separator: $HERE" ;; esac

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "--each-crate reads the crate list from git's index, and $repo is not a git checkout"

# Tracked manifests, shortest path first, keeping each one no kept directory contains -- THEN minus
# GOH_EXCLUDE, on the manifest's path. Nesting first: an excluded crate's testkit is still its
# crate's business, not promoted to a crate of its own. `re.search`, the dialect of every check.
manifests="$(git ls-files -- 'Cargo.toml' '*/Cargo.toml' \
    | awk '{ d = $0; sub(/\/?Cargo\.toml$/, "", d); if (d == "") d = "."; print length(d) "\t" d "\t" $0 }' \
    | sort -n | cut -f2- \
    | awk -F '\t' '{ for (k in kept) if (kept[k] == "." || index($1, kept[k] "/") == 1) next; kept[NR] = $1; print }')"
[ -n "$manifests" ] || die "no tracked Cargo.toml in $repo -- nothing for --each-crate to gate"
crates="$(GOH_EXCLUDE="${GOH_EXCLUDE:-}" python3 -S -c '
import os, re, sys
pattern = os.environ["GOH_EXCLUDE"]
try:
    excluded = re.compile(pattern).search if pattern else lambda _: False
except re.error as e:
    sys.exit(f"GOH_EXCLUDE={pattern!r} is not a regex: {e}")
for line in sys.stdin.read().splitlines():
    d, manifest = line.split("\t", 1)
    if not excluded(manifest):
        print(d)
' <<<"$manifests")" || die "--each-crate could not apply GOH_EXCLUDE (above)"
[ -n "$crates" ] || die "GOH_EXCLUDE='${GOH_EXCLUDE:-}' leaves no crate in $repo for --each-crate to gate"

# Biggest first: the tracked .rs files under each crate.
ordered="$(while IFS= read -r d; do
    printf '%s\t%s\n' "$(git ls-files -- "$d" | grep -c '\.rs$' || true)" "$d"
done <<<"$crates" | sort -t "$(printf '\t')" -k1,1nr -k2,2 | cut -f2-)"

# --list: this selection, nothing run. The same `ordered` the steps below are built from.
[ "$list" = 1 ] && { printf '%s\n' "$ordered"; exit 0; }

gate="bash $(printf %q "$HERE/rust_gate.sh") ."
first="$(head -n1 <<<"$ordered")"
args=(--step "GOH_RUST_GROUPS=repo $gate $(printf %q "$first")")
while IFS= read -r d; do
    args+=(--step "GOH_RUST_GROUPS=crate,coverage $gate $(printf %q "$d")")
done <<<"$ordered"

info "rust gate over $(wc -l <<<"$ordered" | tr -d ' ') crate(s), ${GOH_RUST_JOBS:-4} at a time; repo-wide scans once"
exec bash "$HERE/local_ci.sh" --steps-only --jobs "${GOH_RUST_JOBS:-4}" "${args[@]}" "$repo"
exit
} # parse-guard
