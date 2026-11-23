# shellcheck shell=bash
# land_lock.sh -- sourced, never run. The shell half of lib/land_lock.py's OWNER rule: a land run BY
# the land lock's holder (goh ship's release kit, the lander's batch) does not queue on its own lock.
#
# goh_land_lock_ours LOCK -- status 0 when GOH_LAND_LOCK_OWNER names LOCK and a pid, the lock file's
# holder line names that same pid, and that pid is still the HOLDER: the holder wrote its line once
# the lock was its own, so the file's mtime bounds its start, and a process at the pid that started
# after it is a stranger handed a dead holder's number (GOH.STRAYSIGNAL3; `kill -0` alone read it as
# the holder, and an inherited variable from a dead ship skipped the queue). tools/land.sh calls it;
# tests/test_saved_pid_probes.py.

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/land_lock.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/pid_owner.sh
. "${BASH_SOURCE[0]%/*}/pid_owner.sh" || return 1

goh_land_lock_ours() {
    local lock="${1:?lock path}" owner="${GOH_LAND_LOCK_OWNER:-}" line=""
    local pid="${owner##* }"
    { IFS= read -r line <"$lock"; } 2>/dev/null || true
    [ "${owner% *}" = "$lock" ] && [ -n "$pid" ] && [[ "$line" == *"(pid $pid,"* ]] \
        && goh_pid_alive_since "$pid" "$lock"
}
