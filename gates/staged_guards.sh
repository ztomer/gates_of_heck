#!/usr/bin/env bash
# staged_guards.sh -- a repo's SOURCE GUARDS at commit time: every pytest file that declares it reads
# the repo's own source text, run over the INDEX being committed, when the commit stages what it reads.
#
# THE SHAPE (GOH.CONFIGKEYCOMMIT, GOH.SWIFTGUARDCOMMIT). A test that judges source text -- goh's
# tests/test_config_schema.py (every GOH_* key a gate reads has a docs/config.md row), token-pool's
# tests/test_widget_defaults_guard.py (no widget test builds a panel on the shared xctest defaults
# domain) -- is a check of the STAGED files, but it lived in the pytest suite, which runs only at
# push. So a commit adding GOH_LAND_LOCK_OWNER with no row went in green and was refused ~20 minutes
# into a land gate (ba09c92, 2026-11-15), and token-pool's new WidgetPanel test the same day
# (ef13840). Two patches would each name one test; the property is declared ONCE, in the test file:
#
#     # goh-reads-staged: <pathspec> [<pathspec>...]
#
# on a line of its own (several such lines are one list). The pathspecs are git's (`*.swift`, `widget/Tests/`, `docs/config.md`); a
# commit staging a file one matches -- or the test file itself -- runs the file here; a directive
# with none runs it on every commit. The file still runs at push with the rest of the suite.
#
# THE INDEX, NOT THE WORKING TREE: the guard reads the tree goh_index_view exports (gates/_common.sh),
# so an unstaged fix does not pass a staged violation and an unstaged edit elsewhere does not refuse a
# clean commit. A goh commit already runs from its staged tree's export (GOH.SELFGATE), and goh's
# suite needs that export's binary, so there the export is the view.
#
#   staged_guards.sh [<repo>]   # commit_gate.sh runs it for every repo; exits 0 when nothing is due
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/run_path.sh
. "$HERE/../lib/run_path.sh" || exit 1   # goh_index_tree: never the shared index.lock
repo="${1:-$PWD}"
cd "$repo"
directive='^# goh-reads-staged:'

# The declaring files, from the index (a guard added by this very commit is one of them).
GUARDS=()
while IFS= read -r f; do
    [ -n "$f" ] && GUARDS+=("$f")
done < <( (goh_bind_hook_index; git grep --cached -l -E -e "$directive" -- '*.py') 2>/dev/null || true)
[ "${#GUARDS[@]}" -gt 0 ] || exit 0

# staged_any <pathspec>...: the commit adds, changes, renames or deletes a file one matches.
staged_any() {
    [ -n "$( (goh_bind_hook_index; git diff --cached --name-only --diff-filter=ACMRD -- "$@") 2>/dev/null)" ]
}
DUE=()
for f in "${GUARDS[@]}"; do
    specs="$( (goh_bind_hook_index; git show ":$f") 2>/dev/null | sed -n "s/$directive//p")"
    read -r -d '' -a spec_arr <<<"$specs" || true   # every directive line's pathspecs, as one list
    if [ "${#spec_arr[@]}" -eq 0 ] || staged_any "$f" "${spec_arr[@]}"; then
        DUE+=("$f")
    fi
done
[ "${#DUE[@]}" -gt 0 ] || exit 0

. "$HERE/_common.sh"
# The gated repo's own config, unknown when this script is linted (GOH.SHX):
# shellcheck source=/dev/null
[ -f .gatesrc ] && . ./.gatesrc
. "$HERE/_py.sh"
goh_init "source guards (staged)"
goh_py_runner
goh_py_require pytest
# The runner is resolved in the checkout; the view holds tracked files only (py_staged.sh's rule).
case "${RUN_ARR[0]}" in
    /*) ;;
    */*) RUN_ARR[0]="$PWD/${RUN_ARR[0]#./}"; RUN="${RUN_ARR[*]}" ;;
esac
case "$RUN" in "uv run"*) export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$PWD/.venv}" ;; esac
checkout="$PWD"
tree="$( (goh_bind_hook_index && goh_index_tree) 2>/dev/null || true)"
if [ -n "$tree" ] && [ "${GOH_SELF_GATED:-}" = "$tree|$(pwd -P)" ]; then
    view="$(cd "$HERE/.." && pwd -P)"   # this goh commit's staged-tree export, binary and all
else
    GOH_REPO_ROOT="${GOH_REPO_ROOT:-$(git rev-parse --show-toplevel)}"
    goh_index_view .
    goh_unbind_git  # goh_index_view bound the hook index for itself; a step's own git is not the commit's
    view="$GOH_INDEX_VIEW"
fi
[ -n "$view" ] && [ -d "$view" ] || die "$GOH_NAME: no index view to run the guards in"
. "$HERE/_pytest_root.sh"
goh_pytest_root  # a concurrent suite cannot prune this one's temp dirs
info "${#DUE[@]} declared $(plural_noun "${#DUE[@]}" guard) (# goh-reads-staged:) due for this commit"
# No cache and no bytecode: a goh view is a shared export other gates run from.
goh_step_in "$view" "source guards over the index: ${DUE[*]}" \
    env PYTHONDONTWRITEBYTECODE=1 "${RUN_ARR[@]}" pytest -q -p no:cacheprovider -- "${DUE[@]}"
goh_rm_tree "$PYTEST_DEBUG_TEMPROOT"
cd "$checkout"
goh_done
exit
} # parse-guard
