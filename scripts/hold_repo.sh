#!/usr/bin/env bash
# hold_repo.sh <repo> --reason "<why>" [--lift "<command>"] [--read-only]
#
# Put a repo ON HOLD (lib/repo_hold.py): `goh.hold` in its local git config, valued
# `<date>: <why> -- lift: <command>`. The git gates and the Claude Code guard
# (hooks/claude/hold_guard.py) then refuse every change in it, naming the reason and the lift
# command. --read-only adds the second layer, `chmod -R a-w` (recorded as goh.holdReadOnly, so
# release_hold.sh knows to undo it). The default lift is release_hold.sh; a wrapper that owns
# the hold's way out (archive_repo.sh) names its own.
#
# Uses: an archived repo (archive_repo.sh), an upstream clone that belongs to someone else, a
# repo frozen during a release, a repo an owner ruling parks.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/repo_hold.sh
. "$GOH/lib/repo_hold.sh"

usage() { die "usage: hold_repo.sh <repo> --reason \"<why>\" [--lift \"<command>\"] [--read-only]" 2; }
repo="" reason="" lift="" readonly_=""
while [ $# -gt 0 ]; do
    case "$1" in
        --reason) [ $# -ge 2 ] || usage; reason="$2"; shift 2 ;;
        --lift) [ $# -ge 2 ] || usage; lift="$2"; shift 2 ;;
        --read-only) readonly_=1; shift ;;
        -h|--help) usage ;;
        -*) usage ;;
        *) [ -z "$repo" ] || usage; repo="$1"; shift ;;
    esac
done
[ -n "$repo" ] && [ -n "$reason" ] || usage
case "$reason" in *" -- lift: "*) die "hold_repo.sh: the reason may not contain ' -- lift: '" 2 ;; esac

root="$(cd "$repo" && git rev-parse --show-toplevel 2>/dev/null)" \
    || die "hold_repo.sh: $repo is not a git repository"
[ "$(cd "$repo" && pwd -P)" = "$(cd "$root" && pwd -P)" ] \
    || die "hold_repo.sh: $repo is inside $root -- hold the repository's top, not a directory in it"
if existing="$("$GOH_PY" -S "$GOH/lib/repo_hold.py" show "$root")"; then
    die "hold_repo.sh: $root is already on hold: $existing"
fi
[ -n "$lift" ] || lift="\"$GOH/scripts/release_hold.sh\" $root"

value="$(date +%F): $reason -- lift: $lift"
git -C "$root" config --local goh.hold "$value"
if [ -n "$readonly_" ]; then
    git -C "$root" config --local goh.holdReadOnly true
    chmod -R a-w "$root"
fi
# Read the hold back through the reader every gate uses (G15): "on hold" is what it reports.
"$GOH_PY" -S "$GOH/lib/repo_hold.py" show "$root" >/dev/null \
    || die "hold_repo.sh: $root is not on hold after the write -- the hold was not recorded"
ok "$root is on hold${readonly_:+ and read-only}"
step "why: $reason"
step "lift it: $lift"
exit
} # parse-guard
