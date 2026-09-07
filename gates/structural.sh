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

# An exemption from the CAP is not an exemption from having any bound at all.
# GOH_LINE_EXCLUDE and the shrink-only ratchet are separate mechanisms with
# separate lists, and nothing compared them: monitor had a 619-line test file
# named in LINE_EXCLUDE and absent from its baseline, so it was bounded by
# nothing and no gate said a word. Only runs when the repo points
# GOH_LINE_BASELINE at its ratchet baseline; a repo without one is told the
# check is off rather than passed over in silence.
if [ -n "${GOH_MAX_LINES:-}" ] && [ -n "${GOH_LINE_BASELINE:-}" ]; then
    goh_step "line-cap exemptions carry a ceiling" \
        python3 "$CHECKS/check_exclusion_has_ceiling.py" --max "$GOH_MAX_LINES" \
        --baseline "$GOH_LINE_BASELINE" \
        ${GOH_LINE_EXCLUDE:+--line-exclude "$GOH_LINE_EXCLUDE"} \
        ${GOH_LINE_UNBOUNDED:+--unbounded "$GOH_LINE_UNBOUNDED"}
elif [ -n "${GOH_LINE_EXCLUDE:-}" ]; then
    warn "GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not — an exempted file"
    warn "  is bounded by nothing. Point GOH_LINE_BASELINE at the ratchet baseline."
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

# The companion question to the one below, and a different one: a self-proof shows a gate can
# fail on a VIOLATION; this shows it does not report compliance when its subject is ABSENT. Three
# factory gates had probes and still passed over an empty tree. Nobody edits a gate to break it --
# they rename the directory it reads, and a ratchet then reads a population that dropped to zero
# as every ceiling being met. Full scope only; it is a few seconds per repo because a blind gate
# bails fast.
if [ "$SCOPE" != "--staged" ]; then
    goh_step "gates refuse to pass over an empty tree" \
        python3 "$CHECKS/check_empty_scope.py"
fi

# A gate's self-proof is the only evidence it can fail, and until 2026-09-05 nothing ran one:
# twenty-four probes existed across the estate, all green, none executed by any gate or CI. A
# probe that is never run rots silently while the calibration ratchet goes on counting its gate
# as proven -- an unproven gate under a green light, which is worse than an honestly-missing one.
# Full scope only: it launches one subprocess per probe, which is a push-time cost, not a
# per-commit one.
if [ "$SCOPE" != "--staged" ]; then
    goh_step "gate self-proofs still pass" python3 "$CHECKS/check_probes_pass.py"
fi

# Disk hygiene used to run here on full scope. It no longer does: a 15s du
# stat-storm over host cache/scratch trees does not belong in a commit gate
# (2026-09-04: removed; the watch lives on as scripts/bin/disk_hygiene.sh in
# ~/Projects/scripts, unified with reclaim_build_space.sh which is the fix
# the watch names). Machine-full failures stay diagnosable; they just no
# longer block pushes.

goh_done
