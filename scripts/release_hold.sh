#!/usr/bin/env bash
# release_hold.sh <repo> -- lift a repo's HOLD (lib/repo_hold.py, scripts/hold_repo.sh).
#
# Order matters: a read-only hold (goh.holdReadOnly, or the first archive's goh.archived) is made
# writable FIRST (`chmod -R u+w`), because its config cannot be written until it is; then the hold
# keys are removed. A hold whose recorded lift is ANOTHER command (an archive: unarchive_repo.sh,
# which checks GitHub first) is refused here, naming that command -- the wrapper that owns the way
# out calls this with GOH_HOLD_LIFTED_BY set to its own name.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
GOH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=tui/lib.sh
. "$GOH/tui/lib.sh"
# shellcheck source=lib/repo_hold.sh
. "$GOH/lib/repo_hold.sh"

[ $# -eq 1 ] && [ "${1#-}" = "$1" ] || die "usage: release_hold.sh <repo>" 2
root="$(cd "$1" && git rev-parse --show-toplevel 2>/dev/null)" \
    || die "release_hold.sh: $1 is not a git repository"
shown="$("$GOH_PY" -S "$GOH/lib/repo_hold.py" show "$root")" || { ok "$root is not on hold"; exit 0; }
key="${shown%%	*}" value="${shown#*	}"
lift=""
case "$value" in *" -- lift: "*) lift="${value##* -- lift: }" ;; esac
[ "$key" = "goh.archived" ] && lift="\"$GOH/scripts/unarchive_repo.sh\" $root"
if [ -n "$lift" ] && [[ "$lift" != *release_hold.sh* ]] && [ -z "${GOH_HOLD_LIFTED_BY:-}" ]; then
    die "release_hold.sh: $root's hold is lifted by its own command: $lift"
fi

if [ "$(git -C "$root" config --local --get goh.holdReadOnly || true)" = "true" ] \
    || [ "$key" = "goh.archived" ]; then
    chmod -R u+w "$root"
fi
for k in goh.hold goh.holdReadOnly goh.archived; do
    git -C "$root" config --local --unset-all "$k" 2>/dev/null || true
done
if "$GOH_PY" -S "$GOH/lib/repo_hold.py" show "$root" >/dev/null 2>&1; then
    die "release_hold.sh: $root is still on hold after the keys were unset -- not lifted"
fi
ok "$root: hold lifted (${value})"
exit
} # parse-guard
