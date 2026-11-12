# shellcheck shell=bash
# Sourced. goh_head_export <VAR> <src> [cache] [keep]: set and export VAR to a read-only checkout of
# <src>'s HEAD commit, cached by sha -- so a consumer's gate never judges with a sibling repo's
# WORKING TREE (GOH.HEADEXPORT; the mechanism and its ceiling: lib/head_export.py).
#
#   . "$GOH_DIR/lib/head_export.sh"
#   goh_head_export GAF_DIR "$GAF_SRC" || exit 2
#
# <src> already an export: VAR is <src>. Returns 2, the reason on stderr, when <src> names no commit;
# VAR is then left as it was. Never call it in a $(...): the export must reach the caller.
# shellcheck source=lib/house_python.sh
. "${BASH_SOURCE[0]%/*}/house_python.sh" || return 1

goh_head_export() {
    local _goh_he_out
    _goh_he_out="$("$GOH_PY" -S "${BASH_SOURCE[0]%/*}/head_export.py" "$2" \
        ${3:+--cache "$3"} ${4:+--keep "$4"})" || return 2
    printf -v "$1" '%s' "$_goh_he_out"
    export "${1?}"
}
