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
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
usage: structural.sh [--staged | --full]
  no argument : full-tree checks (same as --full)
  --staged    : staged files only (pre-commit scope, fast)
  --full      : every check (pre-push scope)
EOF
    exit 0
fi
SCOPE="${1:-}"
# Strict scope argument: "" |--full|--staged are the ONLY accepted forms.
# Anything else previously FELL THROUGH to a silent full-tree run — a typo
# (`--stgaed`, `--staged --dry-run`) meant "check everything" without ever
# saying so. Unknown arguments are a usage error naming the accepted forms,
# never a scope guess.
case "$SCOPE" in
    ""|--full|--staged) ;;
    *)
        err "structural.sh: unknown argument '$SCOPE' (accepted: no argument | --full | --staged)"
        exit 2
        ;;
esac
[ $# -le 1 ] || {
    err "structural.sh: unexpected extra arguments: $* (accepted: no argument | --full | --staged)"
    exit 2
}
# Only --staged is a checker-level scope; --full (and no argument) mean
# unrestricted. The scope flag is forwarded to checkers ONLY when it is
# --staged — forwarding --full verbatim made every full run die on argparse.
FWD=""
[ "$SCOPE" = "--staged" ] && FWD="--staged"

# Config comes from the TARGET repo root, wherever the gate was invoked from.
GOH_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
[ -f "$GOH_REPO_ROOT/.gatesrc" ] && . "$GOH_REPO_ROOT/.gatesrc"

goh_init "structural"

# Emoji are a failure state — only the Kare icon set is permitted.
# GOH_EXCLUDE (regex) exempts vendored/generated trees, same as the cap.
# GOH_ALLOW permits extra characters repo-wide (e.g. historical mentions of
# removed glyphs); keep it empty unless the repo genuinely needs it.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "no disallowed emoji (staged)" python3 "$CHECKS/check_no_emoji.py" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${GOH_ALLOW:+--allow "$GOH_ALLOW"}
else
    goh_step "no disallowed emoji" python3 "$CHECKS/check_no_emoji.py" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${GOH_ALLOW:+--allow "$GOH_ALLOW"}
fi

# A conflict marker that reaches a commit is a merge someone walked away from.
goh_step "no conflict markers" python3 "$CHECKS/check_no_conflict_markers.py" ${FWD:+"$FWD"}

# One cap, one name. Repos previously called this check_file_length,
# check_loc and check_file_size, with three different limits.
# Exemption semantics: GOH_EXCLUDE exempts vendored/generated paths from BOTH
# the emoji scan and the cap. GOH_LINE_EXCLUDE is ADDITIVE to the length check
# only — the length exemption is GOH_EXCLUDE ∪ GOH_LINE_EXCLUDE. Paths named
# only by LINE_EXCLUDE stay exempt from the cap but ARE scanned for emoji
# unless GOH_EXCLUDE separately covers them (no consumer ever relied on
# replacement; surveyed 2026-08-25).
GOH_EX="${GOH_EXCLUDE:-}"
if [ -n "${GOH_LINE_EXCLUDE:-}" ]; then
    GOH_EX="${GOH_EX:+${GOH_EX}|}${GOH_LINE_EXCLUDE}"
fi
if [ -n "${GOH_MAX_LINES:-}" ]; then
    goh_step "file length <= ${GOH_MAX_LINES}" \
        python3 "$CHECKS/check_file_length.py" --max "$GOH_MAX_LINES" \
        ${GOH_EX:+--exclude "$GOH_EX"} ${FWD:+"$FWD"}
else
    warn "file-length cap not set — add GOH_MAX_LINES to .gatesrc to enable it"
fi

# Bash is the most-edited language under these gates. bash -n always runs;
# the lint stage degrades to a named warning when shellcheck is not
# installed (swiftlint precedent), never a silent skip. Same GOH_EXCLUDE
# as the emoji scan.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "shell lint (staged)" bash "$CHECKS/check_shell_lint.sh" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
else
    goh_step "shell lint" bash "$CHECKS/check_shell_lint.sh" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
fi

# Committed secrets outrank every other defect class. Same GOH_EXCLUDE;
# revoked vectors suppress per-line with `secret-ok: <reason>`.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "no committed secrets (staged)" python3 "$CHECKS/check_no_secrets.py" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
else
    goh_step "no committed secrets" python3 "$CHECKS/check_no_secrets.py" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
fi

# Disk hygiene used to run here on full scope. It no longer does: a 15s du
# stat-storm over host cache/scratch trees does not belong in a commit gate
# (2026-09-04: removed; the watch lives on as scripts/bin/disk_hygiene.sh in
# ~/Projects/scripts, unified with reclaim_build_space.sh which is the fix
# the watch names). Machine-full failures stay diagnosable; they just no
# longer block pushes.

goh_done
