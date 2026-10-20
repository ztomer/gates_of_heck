#!/usr/bin/env bash
# install_load_reap.sh [install|remove|check] -- the minute job that kills orphaned load spinners.
#
# The plist lives in this repo (launchagents/com.ztomer.goh-load-reap.plist) and is COPIED into
# ~/Library/LaunchAgents, because a plist that exists only in the repo protects nobody: the copies
# drifted before (2026-10-04, two installed plists still named deleted scripts and failed every
# night saying nothing). `--check` compares CONTENT (plutil JSON, so a comment-only difference is
# not drift) and exits 1 naming the drift. `remove` DELETES the installed copy -- the house
# deletes, it does not trash.
#
# It does not fight the reclaim jobs (com.ztomer.reclaim-build-root, com.ztomer.reclaim-build-space):
# a different label, a different script, and this one only ever touches the pids its own registry
# names.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
if [ -f "$ROOT/tui/lib.sh" ]; then
    # shellcheck disable=SC1090
    . "$ROOT/tui/lib.sh"
else
    info() { printf '%s\n' "$*"; }
    ok() { printf '%s\n' "$*"; }
    err() { printf '%s\n' "$*" >&2; }
    die() { printf '%s\n' "$*" >&2; exit 1; }
fi
src="$ROOT/launchagents/com.ztomer.goh-load-reap.plist"
label="com.ztomer.goh-load-reap"
dest_dir="${LAUNCHAGENTS_DIR:-$HOME/Library/LaunchAgents}"
dest="$dest_dir/$(basename "$src")"
mode="${1:-install}"

as_json() { plutil -convert json -o - "$1" 2>/dev/null | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), sort_keys=True))'; }

case "$mode" in
install)
    [ -f "$src" ] || die "$src is missing: the job has no plist to install"
    plutil -lint -s "$src" || die "$src is not a valid plist"
    mkdir -p "$dest_dir"
    cp "$src" "$dest"
    if [ -z "${LAUNCHAGENTS_DIR:-}" ]; then
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$dest"
    fi
    ok "$label: installed ($dest), running every 60s"
    ;;
remove)
    if [ -z "${LAUNCHAGENTS_DIR:-}" ]; then
        launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    fi
    rm -f "$dest"
    ok "$label: removed ($dest deleted)"
    ;;
check)
    if [ ! -f "$dest" ]; then
        err "$label: not installed"
        exit 1
    fi
    if [ "$(as_json "$src")" != "$(as_json "$dest")" ]; then
        err "$label: the installed copy differs from the repo's"
        exit 1
    fi
    ok "$label: installed and current"
    ;;
*)
    die "usage: install_load_reap.sh [install|remove|check]"
    ;;
esac
exit
} # parse-guard
