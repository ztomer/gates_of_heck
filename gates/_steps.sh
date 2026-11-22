# shellcheck shell=bash
# _steps.sh — the step list's PARSING, sourced by gates/local_ci.sh (never run on its own).
#
# A step may carry a bracket prefix, and the prefix carries two things:
#
#   [db,screen] cargo test --workspace          the RESOURCE TAGS: two steps sharing a tag never
#                                                 overlap (the scheduler holds one of them back).
#   [produces=target/debug/app] cargo build     the ARTIFACT DECLARATIONS (gates/_proven.sh): what
#   [consumes=target/debug/app] pytest ...        the step creates, and what it reads. A
#                                                 declaration is not a tag: it names a file.
#   [scope=src+mutants.toml] cargo mutants      the step's declared INPUTS (GOH.PROVENSCOPE): the
#                                                 proven cache keys it on those paths alone.
#
# A path list is separated by `+` (a comma separates the items in the prefix). The declaration is
# part of the step string, so it is part of the key the proven cache computes: a record written
# before a declaration existed can never be found.
#
# WHY IT IS ITS OWN FILE (2026-10-20): the prefix's parsing grew with the declarations and took
# local_ci.sh over the 500-line cap. The cap is a split, never an exemption.
#
# parse_step_prefix <index> <cmd> — sets RUN[index] (the command that runs) and TAGS[index] (the
# comma-delimited tag list, comma-delimited and TRAILING-COMMA'd: the scheduler's `_tags_free`
# walks it with `${rest#*,}` and needs that comma to end the list).

_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
parse_step_prefix() {
    local n="$1" cmd="$2" head items item key paths p tags=""
    PRODUCES[n]=","; CONSUMES[n]=","  # the declared paths, comma-delimited, for _deps_done
    case "$cmd" in
        "["*"] "*)
            head="${cmd%%] *}"
            RUN[n]="${cmd#*] }"
            items="${head#[}"
            while [ -n "$items" ]; do
                item="${items%%,*}"
                if [ "$item" = "$items" ]; then items=""; else items="${items#*,}"; fi
                case "$item" in
                    *=*)
                        key="${item%%=*}"
                        case "$key" in
                            produces|consumes|scope) ;;
                            *) die "step $n: '$key=' is not a declaration (produces=, consumes= and scope= are the three)" ;;
                        esac
                        paths="${item#*=}"
                        while [ -n "$paths" ]; do
                            p="${paths%%+*}"
                            if [ "$p" = "$paths" ]; then paths=""; else paths="${paths#*+}"; fi
                            case "$p" in
                                ""|*" "*) die "step $n: an artifact path holds a space in it (got '$p')" ;;
                            esac
                            if [ "$key" = produces ]; then PRODUCES[n]="${PRODUCES[n]}$p,"
                            elif [ "$key" = consumes ]; then CONSUMES[n]="${CONSUMES[n]}$p,"; fi
                        done
                        ;;
                    *)
                        case "$item" in
                            *[!A-Za-z0-9_.=/,-]*) die "step $n: a tag holds letters, digits and _.=/- only (got '[$head')" ;;
                        esac
                        tags="${tags},${item},"
                        ;;
                esac
            done
            # shellcheck disable=SC2034  # TAGS[] is an output, read by local_ci.sh, which sources this
            TAGS[n]="${tags}"
            ;;
        *)
            # shellcheck disable=SC2034  # RUN[] is an output, read by local_ci.sh, which sources this
            RUN[n]="$cmd" ;;
    esac
}

# _deps_done <i> -- every step declared before i that `produces=` a path i `consumes=` is done
# (GOH.PROVENBUILD gap 2): under GOH_CI_JOBS>1 a producer held back by a busy tag let its consumer,
# declared after it, start first and read nothing. Order, not a tag: two consumers still overlap.
_deps_done() {
    local i="$1" j rest p
    [ "${CONSUMES[i]:-,}" != "," ] || return 0
    for ((j = 1; j < i; j++)); do
        [ "${STATE[j]}" = "done" ] && continue
        rest="${CONSUMES[i]#,}"
        while [ -n "$rest" ]; do
            p="${rest%%,*}"; rest="${rest#*,}"
            case "${PRODUCES[j]:-,}" in *",$p,"*) return 1 ;; esac
        done
    done
    return 0
}

# lci_step_tools <root> -- a step's tool is in a table, or the step is refused (GOH.REQTOOLS): the
# hosted install (`required_tools.py --repo . --install`) refuses the same step, so local and CI
# agree. The rule and the tables are gates/step_tools.py's; before 2026-11-23 it only warns.
lci_step_tools() {
    "$GOH_PY" "$(dirname "${BASH_SOURCE[0]}")/required_tools.py" --repo "$1" --unnamed </dev/null \
        || { err "local_ci: a step runs a tool no table names (above)"; return 1; }
}
