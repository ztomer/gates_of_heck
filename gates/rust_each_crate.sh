#!/usr/bin/env bash
# rust_each_crate.sh — the house Rust gate over EVERY crate of a repo, side by side (BACKLOG P2).
#
#   rust_gate.sh --each-crate [repo]
#
# For a repo of many independent cargo workspaces (media_server: 29 crates under crates/), which
# used to hand-roll this with `xargs -P 4`, a log dir and a `*.failed` sweep. Here it is one
# scheduler, the one every repo's CI steps already use (local_ci.sh): its job budget, its
# declared-order reports, its fail accumulator, its ceilings and its orphan canary.
#
# * THE CRATES: every tracked Cargo.toml not inside another one's directory (a crate's testkit or
#   fuzz manifest is its own crate's business), minus GOH_EXCLUDE. BIGGEST FIRST, by tracked .rs
#   files: the slowest crate (mediaops, 168 s of a 199 s layer) used to start last and set the finish.
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

# Tracked manifests, shortest path first, keeping each one no kept directory contains.
crates="$(git ls-files -- 'Cargo.toml' '*/Cargo.toml' | sed -e 's|/\{0,1\}Cargo.toml$||' -e 's|^$|.|' \
    | { if [ -n "${GOH_EXCLUDE:-}" ]; then grep -vE "$GOH_EXCLUDE" || true; else cat; fi; } \
    | awk '{ print length($0) "\t" $0 }' | sort -n | cut -f2- \
    | awk '{ for (k in kept) if (kept[k] == "." || index($0, kept[k] "/") == 1) next; kept[NR] = $0; print }')"
[ -n "$crates" ] || die "no tracked Cargo.toml in $repo -- nothing for --each-crate to gate"

# Biggest first: the tracked .rs files under each crate.
ordered="$(while IFS= read -r d; do
    printf '%s\t%s\n' "$(git ls-files -- "$d" | grep -c '\.rs$' || true)" "$d"
done <<<"$crates" | sort -t "$(printf '\t')" -k1,1nr -k2,2 | cut -f2-)"

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
