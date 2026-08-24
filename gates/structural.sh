#!/usr/bin/env bash
# structural.sh — the gates every repo gets, whatever it is written in.
#
# Nothing here knows about a toolchain. These are the checks that were being
# re-implemented once per repo (eleven copies of the emoji gate, three
# different names for the file-length cap) and drifting apart as they went.
#
#   structural.sh              # whole tree
#   structural.sh --staged     # staged files only (pre-commit scope)
#
# Config, all optional, read from the TARGET repo's .gatesrc:
#   GOH_MAX_LINES=500          # file-length cap; unset disables the check
#   GOH_LINE_EXCLUDE="re1|re2" # paths exempt from the cap
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

CHECKS="$(cd "$HERE/../checks" && pwd)"
SCOPE="${1:-}"

# Config comes from the TARGET repo root, wherever the gate was invoked from.
GOH_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
[ -f "$GOH_REPO_ROOT/.gatesrc" ] && . "$GOH_REPO_ROOT/.gatesrc"

goh_init "structural"

# Emoji are a failure state — only the Kare icon set is permitted.
# GOH_EXCLUDE (regex) exempts vendored/generated trees, same as the cap.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "no disallowed emoji (staged)" python3 "$CHECKS/check_no_emoji.py" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
else
    goh_step "no disallowed emoji" python3 "$CHECKS/check_no_emoji.py" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
fi

# A conflict marker that reaches a commit is a merge someone walked away from.
goh_step "no conflict markers" python3 "$CHECKS/check_no_conflict_markers.py" ${SCOPE:+"$SCOPE"}

# One cap, one name. Repos previously called this check_file_length,
# check_loc and check_file_size, with three different limits.
# GOH_EXCLUDE is the shared vendor/generated exemption (regex), honored by
# both the emoji and length checks; GOH_LINE_EXCLUDE stays as a length-only
# alias for existing repos.
GOH_EX="${GOH_LINE_EXCLUDE:-${GOH_EXCLUDE:-}}"
if [ -n "${GOH_MAX_LINES:-}" ]; then
    goh_step "file length <= ${GOH_MAX_LINES}" \
        python3 "$CHECKS/check_file_length.py" --max "$GOH_MAX_LINES" \
        ${GOH_EX:+--exclude "$GOH_EX"} ${SCOPE:+"$SCOPE"}
else
    warn "file-length cap not set — add GOH_MAX_LINES to .gatesrc to enable it"
fi

# Scratch ceiling. Not about this repo's code at all -- it is about the machine
# staying able to build it. A full disk fails gates for reasons that look like
# code defects, so it is worth one cheap check per full run.
if [ "$SCOPE" != "--staged" ]; then
    goh_step "disk hygiene" python3 "$CHECKS/check_disk_hygiene.py" \
        --max-scratch-gb "${GOH_MAX_SCRATCH_GB:-25}" \
        --min-free-gb "${GOH_MIN_FREE_GB:-20}"
fi

goh_done
