#!/usr/bin/env bash
# _line_cap.sh — the file-length cap and the two bounds that keep it honest.
# SOURCED by gates/structural.sh, never run.
#
# Its own file because the cap outgrew the script that carries it: adding a step to
# structural.sh kept tripping the 500-line cap, and the answer that costs least is not
# a shorter comment on a step whose reasoning is the whole point of having the gate.
# The cut is at a real boundary and not an arbitrary one — everything below reads
# $CHECKS, $SCOPE, $FWD, $GOH_REPO_ROOT and the repo's own $GOH_* keys, all of which
# structural.sh resolves BEFORE sourcing this, and nothing in structural.sh reads a
# variable set below. So the cluster has no other caller and no other consumer.
#
# The three steps, each answering a DIFFERENT question, which is why they are one
# file rather than three:
#   1. the cap itself                — is any file over GOH_MAX_LINES?
#   2. an exemption carries a ceiling — is every GOH_LINE_EXCLUDE entry bounded?
#   3. the ceilings are ENFORCED      — may a bounded file shrink, never grow?
#
# `$GOH_EX` (the union of GOH_EXCLUDE and GOH_LINE_EXCLUDE) is deliberately NOT used
# by any later step: GOH_LINE_EXCLUDE exempts a path from the CAP only, and a
# vendored crate's prose must still be emoji- and secret-scanned.

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
# `GOH_MAX_LINES=off` is a DECISION, not an omission: a repo whose files are long
# on purpose (the skills corpus, whose references/ ARE the long catalogue) says so
# in its .gatesrc, and gets one info line instead of a warning on every commit.
# A warning that fires on a recorded decision is noise, and noise trains readers to
# skip the line the day it matters. Unset still warns. Normalised to empty here so
# every later `[ -n "$GOH_MAX_LINES" ]` below reads "no cap" and never passes
# `--max off` to a checker.
goh_line_cap_off=""
if [ "${GOH_MAX_LINES:-}" = "off" ]; then
    goh_line_cap_off=1
    GOH_MAX_LINES=""
fi
if [ -n "${GOH_MAX_LINES:-}" ]; then
    goh_step "file length <= ${GOH_MAX_LINES}" \
        python3 "$CHECKS/check_file_length.py" --max "$GOH_MAX_LINES" \
        ${GOH_EX:+--exclude "$GOH_EX"} ${FWD:+"$FWD"}
elif [ -n "$goh_line_cap_off" ]; then
    info "file-length cap off — declared in .gatesrc (GOH_MAX_LINES=off)"
else
    warn "file-length cap not set — add GOH_MAX_LINES to .gatesrc to enable it"
fi

# The two line-cap bounds below read a baseline and measure files, so at
# --staged they run INSIDE the index view (goh_index_view): the commit is what
# is judged, not the tree around it. Both run from the repo root (the view's
# root at --staged), never the caller's cwd: the ratchet's `[ -f ]` and the
# checkers' relative paths once resolved against a subdirectory and skipped
# the ratchet without a word.
goh_line_root="$GOH_REPO_ROOT"
if [ "$SCOPE" = "--staged" ] && [ -n "${GOH_LINE_BASELINE:-}" ]; then
    goh_index_view "$GOH_REPO_ROOT" || die "structural: the repo root is outside the repo?"
    goh_line_root="$GOH_INDEX_VIEW"
fi
case "${GOH_LINE_BASELINE:-}" in
    /*) goh_line_baseline_path="$GOH_LINE_BASELINE" ;;
    *)  goh_line_baseline_path="$goh_line_root/${GOH_LINE_BASELINE:-}" ;;
esac

# An exemption from the CAP is not an exemption from having any bound at all.
# GOH_LINE_EXCLUDE and the shrink-only ratchet are separate mechanisms with
# separate lists, and nothing compared them: monitor had a 619-line test file
# named in LINE_EXCLUDE and absent from its baseline, so it was bounded by
# nothing and no gate said a word. Only runs when the repo points
# GOH_LINE_BASELINE at its ratchet baseline; a repo without one is told the
# check is off rather than passed over in silence.
if [ -n "${GOH_MAX_LINES:-}" ] && [ -n "${GOH_LINE_BASELINE:-}" ]; then
    goh_step_in "$goh_line_root" "line-cap exemptions carry a ceiling" \
        python3 "$CHECKS/check_exclusion_has_ceiling.py" --max "$GOH_MAX_LINES" \
        --baseline "$GOH_LINE_BASELINE" \
        ${GOH_LINE_EXCLUDE:+--line-exclude "$GOH_LINE_EXCLUDE"} \
        ${GOH_LINE_UNBOUNDED:+--unbounded "$GOH_LINE_UNBOUNDED"}
elif [ -n "${GOH_LINE_EXCLUDE:-}" ]; then
    warn "GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not — an exempted file"
    warn "  is bounded by nothing. Point GOH_LINE_BASELINE at the ratchet baseline."
fi

# ...and the ceilings are ENFORCED here, not left to each repo's own gate
# script. Until 2026-09-14 only the "carries a ceiling" check above ran in
# the shared layer; the shrink-only ratchet over `wc -l` was wired per repo,
# so a repo that listed ceilings and never ran the ratchet was, again,
# bounded by nothing. Files named in the baseline may come down and may not
# grow past their number.
if [ -n "${GOH_LINE_BASELINE:-}" ] && [ -f "$goh_line_baseline_path" ]; then
    goh_step_in "$goh_line_root" "cap-exempt files within their ceilings" \
        python3 "$CHECKS/check_baseline_ratchet.py" --baseline "$GOH_LINE_BASELINE" \
        --current-from-command "python3 '$CHECKS/loc_of_baseline_files.py' '$GOH_LINE_BASELINE'"
fi
