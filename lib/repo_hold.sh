# shellcheck shell=bash
# lib/repo_hold.sh -- the git gates' half of THE HOLD (lib/repo_hold.py): a repo whose local git config
# carries `goh.hold` (or the first archive's `goh.archived`) is not committed to, merged into, patched
# or pushed from. Sourced by gates/commit_gate.sh, commit_msg.sh, sequencer_gate.sh and push_gate.sh,
# so every installed repo has it at runtime with no re-install.
#
#   goh_refuse_held <repo root> <what is refused>   # 0 when not held; 1 with the reason and lift printed
if [ -z "${GOH_PY:-}" ]; then
    # shellcheck source=lib/house_python.sh
    . "${BASH_SOURCE[0]%/*}/house_python.sh" || return 1
fi
goh_refuse_held() {
    "$GOH_PY" -S "${BASH_SOURCE[0]%/*}/repo_hold.py" check "$1" --action "$2"
}
