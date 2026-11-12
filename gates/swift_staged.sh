#!/usr/bin/env bash
# swift_staged.sh -- the CHEAP half of swift_gate.sh, at COMMIT time, on the staged .swift files only.
#
#   swiftlint lint --strict on staged *.swift, under the same config and baseline swift_gate.sh uses
#
#   swift_staged.sh            # from the repo root
#   swift_staged.sh <repo>
#
# Rule 17 (GOH.STAGEDPARITY, from koffee_big's KB.GATEPARITY 8f415fd): the push ran
# `swiftlint --strict`, the commit did not, so a commit went in green and its push was refused --
# twice in one koffee_big lane, and the same hole stood in nine repos. gates/commit_gate.sh runs this
# for every repo with tracked .swift, so no repo's gate.sh has to remember it.
#
# WHICH CONFIG. swift_gate.sh runs in the directory a repo hands it (`swift_gate.sh ./ui` in
# antiknob, `./widget` in token-pool), where that directory's .swiftlint.yml and .gatesrc rule. So
# each staged file is linted from its nearest ancestor holding a .swiftlint.yml (the repo root when
# none does), and that directory's .gatesrc names its GOH_SWIFT_LINT_BASELINE, read through the same
# reconciler swift_gate.sh uses (gates/swift_lint_baseline.py), scoped to the files linted so the
# rest of the baseline is not reported as vanished. `--force-exclude` keeps a config's `excluded`
# binding on explicit paths, as it binds the push's directory walk.
#
# Exits 0 when nothing Swift is staged, and when every staged file is excluded by its config.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# One lint root, run as a goh_step so its output is held and its failure framed:
#   swift_staged.sh --lint-root <dir> <baseline-or-empty> <file>...   (files relative to <dir>)
# swiftlint has no "zero lintable files is fine" flag (0.65.1 refuses
# --allow-zero-lintable-files): a commit whose staged files are all `excluded` gets exit 1 and
# "No lintable files found", which is a pass here, and only that exit with only that message.
if [ "${1:-}" = "--lint-root" ]; then
    dir="$2" baseline="$3"
    shift 3
    cd "$dir"
    errs="$(mktemp "${TMPDIR:-/tmp}/goh-swiftlint-err.XXXXXX")"
    report="$(mktemp "${TMPDIR:-/tmp}/goh-swiftlint-report.XXXXXX")"
    trap 'rm -f "$errs" "$report"' EXIT
    rc=0
    if [ -n "$baseline" ]; then
        swiftlint lint --strict --quiet --force-exclude --reporter json "$@" >"$report" 2>"$errs" || rc=$?
    else
        swiftlint lint --strict --quiet --force-exclude "$@" 2>"$errs" || rc=$?
    fi
    if [ "$rc" -eq 1 ] && [ "$(grep -c . "$errs")" -eq 1 ] && grep -q '^Error: No lintable files found' "$errs"; then
        printf '→ every staged .swift here is excluded by %s/.swiftlint.yml\n' "$dir"
        exit 0
    fi
    cat "$errs" >&2
    [ -n "$baseline" ] || exit "$rc"
    [ -f "$baseline" ] || { printf '✗ GOH_SWIFT_LINT_BASELINE points at nothing: %s/%s\n' "$dir" "$baseline" >&2; exit 1; }
    # shellcheck source=gates/_py.sh
    . "$HERE/_py.sh"
    exec "$GOH_PY" "$HERE/swift_lint_baseline.py" --baseline "$baseline" --report "$report" \
        --root . --swiftlint-rc "$rc" --scope "$@"
fi

. "$HERE/_common.sh"
repo="${1:-$PWD}"
cd "$repo"
# The gated repo's own config, unknown when this script is linted (GOH.SHX):
# shellcheck source=/dev/null
[ -f .gatesrc ] && . ./.gatesrc
goh_init "swift (staged)"
# commit_gate.sh runs this twin itself unless the repo's gate already did, for THIS tree.
[ -z "${GOH_STAGED_TWIN_MARK:-}" ] || printf 'swift_staged\t%s\n' "$(pwd -P)" >>"$GOH_STAGED_TWIN_MARK"

STAGED=()
while IFS= read -r f; do
    [ -n "$f" ] && STAGED+=("$f")
done < <( (goh_bind_hook_index  # the index being committed (gates/_git_env.sh)
          git diff --cached --name-only --diff-filter=ACMR --relative -- '*.swift') 2>/dev/null || true)
# macOS ships bash 3.2: no mapfile, and "${arr[@]}" on an empty array trips `set -u` there.
if [ "${#STAGED[@]}" -eq 0 ]; then
    info "no staged .swift files"
    goh_done
    exit 0
fi
goh_require swiftlint "brew install swiftlint"

# Each file's lint root: its nearest ancestor (inside the repo) with a .swiftlint.yml, else ".".
pairs=""
for f in "${STAGED[@]}"; do
    d="$(dirname "$f")"
    while [ "$d" != "." ] && [ ! -f "$d/.swiftlint.yml" ]; do d="$(dirname "$d")"; done
    pairs="$pairs$d	$f
"
done
roots="$(printf '%s' "$pairs" | cut -f1 | sort -u)"

# excluded_above <root> <file>...: an ancestor's .swiftlint.yml `excluded` covers every one of these
# files. The push's directory walk from that ancestor never descends there, so neither does this:
# goh's own tests/fixtures/swift_baseline_project carries a .swiftlint.yml and is excluded from the
# root's. swiftlint is the oracle (its glob rules are its own): "No lintable files found" from the
# ancestor, under --force-exclude, is the only exit that means "all excluded". A group the ancestor
# excludes only in part is linted whole, under its own config -- stricter, never blind.
excluded_above() {
    local root="$1" a rc
    shift
    a="$root"
    while [ "$a" != "." ]; do
        a="$(dirname "$a")"
        [ -f "$a/.swiftlint.yml" ] || continue
        rc=0
        (cd "$a" && swiftlint lint --quiet --force-exclude --reporter json "${@/#/${root#"$a"/}/}") \
            >/dev/null 2>"$GOH_LOG.above" || rc=$?
        if [ "$rc" -eq 1 ] && grep -q '^Error: No lintable files found' "$GOH_LOG.above"; then
            rm -f "$GOH_LOG.above"
            info "every staged .swift in $root is excluded by $a/.swiftlint.yml"
            return 0
        fi
        rm -f "$GOH_LOG.above"
    done
    return 1
}
# in_included <root> <file relative to root>: the file sits under one of the `included:` roots of
# <root>/.swiftlint.yml, or that config names none (the whole tree is in scope). swiftlint given EXPLICIT
# paths ignores `included`, while the push walks only those roots (ZoneWM: `included: Sources`, so its
# Tests/ are never linted): without this a commit was refused for findings in a file the push never
# reads (ZW.WALLCLOCK2). An inline list (`included: [A, B]`) is not parsed and means "lint it": stricter.
in_included() {
    local cfg="$1/.swiftlint.yml" f="$2" inc p
    [ -f "$cfg" ] || return 0
    grep -q '^included:[[:space:]]*[^[:space:]#]' "$cfg" && return 0
    inc="$(awk '/^[^[:space:]#]/ { on = ($0 ~ /^included:/) }
        on && /^[[:space:]]*-[[:space:]]+/ { sub(/^[[:space:]]*-[[:space:]]+/, ""); gsub(/["'"'"']/, "");
            sub(/[[:space:]]*(#.*)?$/, ""); sub(/^\.\//, ""); sub(/\/$/, ""); print }' "$cfg")"
    [ -n "$inc" ] || return 0
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        case "$f" in "$p" | "$p"/*) return 0 ;; esac
    done <<<"$inc"
    return 1
}
while IFS= read -r root; do
    [ -n "$root" ] || continue
    files=()
    while IFS='	' read -r d f; do
        [ "$d" = "$root" ] || continue
        if [ "$root" = "." ]; then files+=("$f"); else files+=("${f#"$root"/}"); fi
    done <<<"$pairs"
    kept=()
    for f in "${files[@]}"; do
        in_included "$root" "$f" && kept+=("$f")
    done
    if [ "${#kept[@]}" -eq 0 ]; then
        info "every staged .swift in $root is outside the included roots of $root/.swiftlint.yml"
        continue
    fi
    files=("${kept[@]}")
    [ "$root" = "." ] || ! excluded_above "$root" "${files[@]}" || continue
    # That root's own baseline, as swift_gate.sh run there would read it. The gated repo's
    # .gatesrc, unknown when this script is linted (GOH.SHX), in a child: `bash -c`, as land.sh reads it.
    baseline="$(cd "$root" && bash -c 'unset GOH_SWIFT_LINT_BASELINE
        [ ! -f .gatesrc ] || . ./.gatesrc >/dev/null 2>&1 </dev/null
        printf %s "${GOH_SWIFT_LINT_BASELINE-}"')" || die "cannot read $root/.gatesrc"
    where=""
    [ "$root" = "." ] || where=" in $root"
    goh_step "swiftlint --strict (staged$where${baseline:+, baseline: $(basename "$baseline")})" \
        bash "$HERE/swift_staged.sh" --lint-root "$root" "$baseline" "${files[@]}"
done <<<"$roots"
goh_done
exit
} # parse-guard
