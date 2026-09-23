#!/usr/bin/env bash
# check_shell_lint.sh — bash -n + shellcheck --severity=error over shell files.
#
# Bash is the most-edited language under these gates and was the only
# unlinted one (Python gets ruff on every run). Two stages, both load-bearing:
#   1. `bash -n` — syntax. Always available, always runs.
#   2. `shellcheck --severity=error` — error-level findings only (warnings
#      stay advisory; the tree has deliberate word-splitting with its own
#      disables). Missing shellcheck degrades to stage 1 with a NAMED
#      warning (the swiftlint precedent), never a silent pass.
#
# Scope: tracked `*.sh` plus extensionless `hooks/*` in full mode; the git
# index (`git show :path` into a temp dir, display names preserved) in
# --staged mode — the house index-truth rule.
#
#   check_shell_lint.sh                        # all tracked shell files
#   check_shell_lint.sh --staged               # staged files only
#   check_shell_lint.sh --exclude '^vendor/'   # skip vendored trees
#
# Exit: 0 clean · 1 violation found · 2 usage/config error.
# macOS stock bash 3.2 compatible (no assoc arrays, no mapfile).
set -euo pipefail

usage() {
    cat <<'EOF'
usage: check_shell_lint.sh [--staged] [--exclude RE]
EOF
}

STAGED=0
EXCLUDE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --staged) STAGED=1; shift ;;
        --exclude)
            [ $# -ge 2 ] || {
                printf '✗ [shell_lint] --exclude needs a value\n' >&2; exit 2; }
            EXCLUDE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        --*) printf '✗ [shell_lint] unknown option: %s\n' "$1" >&2; exit 2 ;;
        *)   printf '✗ [shell_lint] unexpected argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$ROOT" ]; then
    printf '[shell_lint] not a git repo — skipping\n'
    exit 0
fi

TMPD=""
cleanup() { [ -z "$TMPD" ] || rm -rf "$TMPD"; }
trap cleanup EXIT

# DISP[i] is the repo-relative path shown to the user; SRC[i] the file the
# tools actually read (worktree path, or a temp copy of the index blob).
DISP=()
SRC=()
N=0
add_file() { DISP[$N]="$1"; SRC[$N]="$2"; N=$((N + 1)); }

if [ "$STAGED" = "1" ]; then
    TMPD="$(mktemp -d "${TMPDIR:-/tmp}/shell-lint.XXXXXX")"
    PICKED=()
    while IFS= read -r -d '' f; do
        case "$f" in
            *.sh|hooks/*) ;;
            *) continue ;;
        esac
        if [ -n "$EXCLUDE" ] && [[ "$f" =~ $EXCLUDE ]]; then continue; fi
        PICKED+=("$f")
    done < <(git -C "$ROOT" diff --cached --name-only -z --diff-filter=ACMR)
    # ONE export of every picked index blob (rust-port Phase 3d): it was one
    # `git show` per file. The copies keep their repo paths under $TMPD/index,
    # so the tools read the index's bytes and relative `source` lines resolve
    # as they do in the tree. A path the index no longer holds (a race with
    # the commit) is absent afterwards and skipped, as `git show` failing was.
    if [ "${#PICKED[@]}" -gt 0 ]; then
        printf '%s\0' "${PICKED[@]}" \
            | git -C "$ROOT" checkout-index -z --stdin --prefix="$TMPD/index/" 2>/dev/null || true
        for f in "${PICKED[@]}"; do
            [ -f "$TMPD/index/$f" ] && add_file "$f" "$TMPD/index/$f"
        done
    fi
else
    while IFS= read -r -d '' f; do
        case "$f" in
            *.sh|hooks/*) ;;
            *) continue ;;
        esac
        if [ -n "$EXCLUDE" ] && [[ "$f" =~ $EXCLUDE ]]; then continue; fi
        add_file "$f" "$ROOT/$f"
    done < <(git -C "$ROOT" ls-files -z)
fi

if [ "$N" -eq 0 ]; then
    printf '✓ [shell_lint] OK — no shell files in scope\n'
    exit 0
fi

HAVE_SC=0
command -v shellcheck >/dev/null 2>&1 && HAVE_SC=1
if [ "$HAVE_SC" -eq 0 ]; then
    printf '⚠ [shell_lint] shellcheck not installed — syntax check only (brew install shellcheck)\n' >&2
fi

FAIL=()
# Distinct failing files for the "%d file(s)" report (findings are per
# line, files per path — a linear dedup, no assoc arrays on bash 3.2).
FAILED=()
F=0
mark_failed() {
    m=0
    while [ "$m" -lt "$F" ]; do
        [ "${FAILED[$m]}" = "$1" ] && return 0
        m=$((m + 1))
    done
    FAILED[$F]="$1"
    F=$((F + 1))
}
# Stage 1 (bash -n) runs over every file first so a syntax error fails
# fast without paying for a linter spawn; stage 2 is ONE shellcheck
# process over all syntax-clean files, not one process per file
# (interleaved A/B 2026-09-23: per-file loop ~2.2-3.0 s, single
# invocation ~1.6-2.2 s on 42 files — the spawns, not the lint, were
# the ~700 ms gap). Files are appended by counter, never expanded as
# empty arrays are never expanded under `set -u` (bash 3.2 unsafe).
SYNTAX_FAIL=()
CLEAN_DISP=()
CLEAN_SRC=()
S=0
C=0
i=0
while [ "$i" -lt "$N" ]; do
    n_err=""
    if ! n_err="$(bash -n "${SRC[$i]}" 2>&1 >/dev/null)"; then
        SYNTAX_FAIL[$S]="${DISP[$i]}: bash -n: $(printf '%s' "$n_err" | head -1)"
        S=$((S + 1))
        mark_failed "${DISP[$i]}"
    else
        CLEAN_DISP[$C]="${DISP[$i]}"
        CLEAN_SRC[$C]="${SRC[$i]}"
        C=$((C + 1))
    fi
    i=$((i + 1))
done
k=0
while [ "$k" -lt "$S" ]; do
    FAIL[${#FAIL[@]}]="${SYNTAX_FAIL[$k]}"
    k=$((k + 1))
done
if [ "$HAVE_SC" -eq 1 ] && [ "$C" -gt 0 ]; then
    sc_out="$(shellcheck --severity=error --format=gcc "${CLEAN_SRC[@]}" 2>&1 || true)"
    if [ -n "$sc_out" ]; then
        # Display names back over temp/staged paths, one file at a time
        # (a single global replace would let a path that is a prefix of
        # another rewrite its sibling).
        j=0
        while [ "$j" -lt "$C" ]; do
            sc_out="${sc_out//${CLEAN_SRC[$j]}/${CLEAN_DISP[$j]}}"
            j=$((j + 1))
        done
        while IFS= read -r line; do
            FAIL[${#FAIL[@]}]="$line"
            mark_failed "${line%%:*}"
        done <<<"$sc_out"
    fi
fi

if [ "$F" -gt 0 ]; then
    scope="tracked"
    [ "$STAGED" = "1" ] && scope="staged"
    printf '✗ [shell_lint] %d file(s) failed (%s):\n' "$F" "$scope" >&2
    for f in "${FAIL[@]}"; do printf '  %s\n' "$f" >&2; done
    exit 1
fi

scope="tracked"
[ "$STAGED" = "1" ] && scope="staged"
extra=""
[ "$HAVE_SC" -eq 0 ] && extra=" (bash -n only; shellcheck not installed)"
printf '✓ [shell_lint] OK — %d %s shell files clean%s\n' "$N" "$scope" "$extra"
exit 0
