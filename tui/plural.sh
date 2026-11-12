# shellcheck shell=bash
# tui/plural.sh -- a count meets its noun, so no message hedges with `file(s)` (GOH.PLURALHEDGE;
# the house gate `goh plural-hedge` refuses the hedge). Sourced by tui/lib.sh, and directly by a
# gate that may have sourced a consumer's VENDORED lib.sh, which predates these names.
# Exactly one is singular; the plural defaults to the singular plus s. No trailing newline:
#   ok "$(counted "$n" file) checked"      err "$n $(plural_noun "$n" entry entries) open"
[ ! -f "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" ] || { . "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"; }   # a lone tui/ copy has none
plural_noun() {
    local n="${1//[[:space:]]/}"
    if [ "$n" = 1 ]; then printf '%s' "$2"; else printf '%s' "${3:-${2}s}"; fi
}
counted() { local n="${1//[[:space:]]/}"; printf '%s %s' "$n" "$(plural_noun "$n" "$2" "${3:-}")"; }
