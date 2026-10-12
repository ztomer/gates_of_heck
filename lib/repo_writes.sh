# shellcheck shell=bash
# repo_writes.sh -- what a gate run wrote into the repository it was gating (BACKLOG 6.11).
#
# A gate runs in an export worktree, but the worktree shares its repository's `.git/config` and refs:
# the games metarepo's `origin` read `https://elsewhere.invalid/x.git` from 2026-10-08 until it was
# repaired by hand, written by a probe in that commit's own hook run, and nothing compared the
# config before and after. So the push gate snapshots both before the gate and reads them again
# after it:
#
#   * a changed `.git/config` (a remote URL, an include, any key) REFUSES the push, naming each key
#     with its old and new value, userinfo redacted;
#   * a moved ref is NAMED, not refused: another session commits to the same repository while a long
#     gate runs (ZoneWM, D-0211), so a ref that moved is often someone else's work, and refusing it
#     would blame the gate for it. `refs/remotes` is left out: a fetch moves those legitimately.
#
#   . "$GOH/lib/repo_writes.sh"
#   before="$(goh_repo_snapshot "$root")"  ...gate...  goh_repo_writes "$root" "$before" || exit 1

# goh_repo_snapshot ROOT -- the repository's own config and its refs, sorted: `config KEY=VALUE`
# lines, then `ref NAME SHA` lines.
goh_repo_snapshot() {
    git -C "$1" config --local --list 2>/dev/null | LC_ALL=C sort | sed 's/^/config /'
    git -C "$1" for-each-ref --exclude=refs/remotes --format='ref %(refname) %(objectname)' 2>/dev/null \
        | LC_ALL=C sort
}

# _goh_redact -- a URL's userinfo replaced, so a token in a remote never reaches a log.
_goh_redact() {
    sed -E 's#://[^/@[:space:]]+@#://***@#g'
}

# goh_repo_writes ROOT BEFORE -- reports what changed since BEFORE (goh_repo_snapshot's output).
# Returns 1 when the config changed; a moved ref is a warning and returns 0.
goh_repo_writes() {
    local after changed
    after="$(goh_repo_snapshot "$1")"
    [ "$after" = "$2" ] && return 0
    changed="$(diff <(printf '%s\n' "$2") <(printf '%s\n' "$after") | sed -n 's/^\([<>]\) /\1 /p' | _goh_redact)"
    if grep -q '^[<>] ref ' <<<"$changed"; then
        warn "the gate run moved refs in $(basename "$1") (another session's commit, or the gate's own write):"
        grep '^[<>] ref ' <<<"$changed" | sed 's/^< ref /    was: /; s/^> ref /    now: /' >&2
    fi
    if grep -q '^[<>] config ' <<<"$changed"; then
        err "the gate run wrote $(basename "$1")'s own .git/config -- a gate never writes the repository it gates:"
        grep '^[<>] config ' <<<"$changed" | sed 's/^< config /    was: /; s/^> config /    now: /' >&2
        return 1
    fi
    return 0
}
