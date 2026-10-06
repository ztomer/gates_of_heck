# shellcheck shell=bash
# _proven.sh — the proven-step cache, sourced by gates/proven.sh (the CLI a hook calls) and
# gates/local_ci.sh (the runner behind `tools/gate.sh --full`). One implementation, so the key
# a pre-commit hook records and the key the pre-push run looks up cannot drift apart. Why the
# cache exists and why it is honest: see the header of gates/proven.sh.
#
# Works under `set -u` with or without `-e` (local_ci.sh runs without it): every failure is
# handled by return code, and no function changes shell options. Every git call on the REPO is
# made from the current directory and honours GIT_INDEX_FILE, because inside a pre-commit hook
# that variable names the index being committed -- which is exactly the tree to key on. Every git
# call on a gates_of_heck checkout strips the GIT_* variables first, or a hook's index would be
# read as goh's.

# shellcheck source=gates/_hash.sh
. "$(dirname "${BASH_SOURCE[0]}")/_hash.sh"
PROVEN_GOH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Environment variables whose values change what a gate step concludes, keyed by default. The
# repo adds its own with GOH_PROVEN_ENV (space-separated names) in .gatesrc.
PROVEN_ENV_DEFAULT="CI RUSTFLAGS RUSTDOCFLAGS CARGO_BUILD_TARGET PYTHONPATH"

# proven_settings — validate the knobs once; prints the error and returns 2 on a bad value.
# GOH_PROVEN=0 disables the cache; GOH_PROVEN_TTL_S is the record lifetime in seconds.
proven_settings() {
    case "${GOH_PROVEN_TTL_S:-86400}" in
        ""|*[!0-9]*)
            printf 'GOH_PROVEN_TTL_S must be a non-negative integer of seconds (got %s)\n' \
                "'${GOH_PROVEN_TTL_S}'" >&2
            return 2 ;;
    esac
    PROVEN_TTL="${GOH_PROVEN_TTL_S:-86400}"
    case "${GOH_PROVEN:-1}" in
        0) PROVEN_ON=0 ;;
        *) PROVEN_ON=1 ;;
    esac
    proven_identity_prime
    if [ -z "${_proven_epoch0:-}" ]; then
        _proven_epoch0="$(date +%s)" _proven_seconds0="$SECONDS"
    fi
    return 0
}

# proven_now — epoch seconds. GOH_PROVEN_NOW is the clock seam the tests use to age a record.
# The clock, read ONCE per gate (proven_settings) and advanced by bash's own SECONDS: a `date`
# spawn per lookup and record, against a TTL of a day, bought nothing. GOH_PROVEN_NOW pins it.
proven_now() {
    if [ -n "${GOH_PROVEN_NOW:-}" ]; then
        printf '%s\n' "$GOH_PROVEN_NOW"
    elif [ -n "${_proven_epoch0:-}" ]; then
        printf '%s\n' "$((_proven_epoch0 + SECONDS - _proven_seconds0))"
    else
        date +%s
    fi
}

# proven_tree — the index tree, printed, ONLY when the working tree equals the index: no
# unstaged change and no untracked file that is not ignored. Anything else returns 1: the bytes
# a step would test are then no tree git can name, so there is nothing a later run could match.
proven_tree() {
    local st line
    st="$(git --no-optional-locks status --porcelain=v1 --untracked-files=all 2>/dev/null)" \
        || return 1
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        case "$line" in
            '??'*) return 1 ;;       # untracked, not ignored: the build may read it
            ?' '*) ;;                # staged only: the working tree holds the index's bytes
            *) return 1 ;;           # unstaged edit, or an unmerged path
        esac
    done <<EOF
$st
EOF
    git write-tree 2>/dev/null
}

# _proven_goh_git <dir> <git args...> — git on a gates_of_heck checkout, hook variables stripped.
# The list is git's own (`--local-env-vars`, what it clears entering a submodule), never a copy:
# the hand-kept one this replaced had drifted to 7 of git's 15.
_proven_goh_git() {
    (
        # shellcheck disable=SC2046  # word-splitting the name list is the point
        unset ${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars)}
        git -C "$@"
    )
}

# _proven_goh_identity <dir> — HEAD, the diff against it (staged and unstaged), and the content
# of every untracked file: work in progress on the gates invalidates what they proved.
_proven_goh_identity() {
    local d="$1" top head diff untracked
    if ! top="$(_proven_goh_git "$d" rev-parse --show-toplevel 2>/dev/null)"; then
        printf 'goh not-a-repo %s\n' "$(cd "$d" 2>/dev/null && pwd -P)"
        return 0
    fi
    head="$(_proven_goh_git "$top" rev-parse HEAD 2>/dev/null || echo none)"
    diff="$(_proven_goh_git "$top" diff HEAD --binary 2>/dev/null | hash_hex /dev/stdin)"
    # Bytecode caches are NOT work in progress. Every gate that runs a Python
    # checker out of this checkout writes __pycache__/ into it, and a checkout
    # that does not gitignore bytecode (an installed copy, a CI export — anything
    # without THIS repo's .gitignore) then sees its identity change PART-WAY
    # THROUGH A PUSH: the pre-commit hook proves a step, the pre-push worktree
    # gate creates the cache, and every subsequent lookup misses. Measured
    # 2026-10-01 on tests/test_proven.py's gates copy, where a push_gate.sh that
    # ran one extra checker re-ran a step the checkout had already proved.
    #
    # Excluded by PATH SEGMENT, not by substring: `vendor/…/__pycache__` and a
    # file genuinely named `__pycache__.py` are not the same thing, and a
    # substring filter silently drops real work. `:(exclude)` pathspecs need
    # magic pathspec off, which `ls-files --others` has not always accepted, so
    # filter the list here.
    untracked="$(_proven_goh_git "$top" ls-files --others --exclude-standard 2>/dev/null \
        | grep -vE '(^|/)__pycache__/' \
        | _proven_goh_git "$top" hash-object --stdin-paths 2>/dev/null | hash_hex /dev/stdin)"
    printf 'goh %s %s %s\n' "$head" "$diff" "$untracked"
}

# _proven_tool <name> <version command...> — one toolchain line; absent tools a fixed token.
_proven_tool() {
    local name="$1"; shift
    if command -v "$name" >/dev/null 2>&1; then
        printf 'tool %s %s\n' "$name" "$("$@" 2>&1 </dev/null | head -n 1)"
    else
        printf 'tool %s absent\n' "$name"
    fi
}

# The run-constant half of proven_identity: the gates checkout's identity and the toolchains.
# ~110 ms, and every step's key was computed twice in every group of every gate (a media_server
# push: ~174 times). `proven_settings` asks it ONCE and exports it for the same working
# directory, PATH, toolchain override, gates root and GOH_LIVE mode; a key computed anywhere else
# asks again (tests/test_proven_identity_once.py).
_proven_memo_key() {
    printf '%s|%s|%s|%s|%s|%s' "$PWD" "$PATH" "${RUSTUP_TOOLCHAIN:-}" "${PROVEN_GOH_ROOT:-}" \
        "${GOH_DIR:-}" "${GOH_LIVE:-}"
}

proven_identity_prime() {
    [ "${PROVEN_ON:-1}" = 1 ] || return 0
    # Only where the export can reach the steps: a `$(proven_settings)` that only validates (as
    # local_ci.sh does, to capture the message) would compute it for nobody.
    [ "${BASH_SUBSHELL:-0}" = 0 ] || return 0
    local k
    k="$(_proven_memo_key)"
    [ "${GOH_PROVEN_IDENTITY_FOR:-}" = "$k" ] && return 0
    GOH_PROVEN_IDENTITY="$(_proven_identity_slow)"
    GOH_PROVEN_IDENTITY_FOR="$k"
    export GOH_PROVEN_IDENTITY GOH_PROVEN_IDENTITY_FOR
}

# proven_identity — everything besides the tree and the step that decides a step's verdict.
proven_identity() {
    local name
    if [ "${GOH_PROVEN_IDENTITY_FOR:-}" = "$(_proven_memo_key)" ]; then
        printf '%s\n' "$GOH_PROVEN_IDENTITY"
    else
        _proven_identity_slow
    fi
    _proven_identity_live
}

_proven_identity_slow() {
    local d seen="" files
    for d in "$PROVEN_GOH_ROOT" "${GOH_DIR:-}"; do
        [ -n "$d" ] && [ -d "$d" ] || continue
        d="$(cd "$d" && pwd -P)"
        case " $seen " in *" $d "*) continue ;; esac
        seen="$seen $d"
        _proven_goh_identity "$d"
    done
    # Toolchains, for the languages the repo tracks. `swift --version` alone costs ~0.7 s and
    # the key is computed twice per step, so a repo with no Swift does not pay for Swift's.
    files="$(git ls-files 2>/dev/null)"
    # Here-strings, not `printf | grep -q`: under pipefail an early-exiting grep SIGPIPEs the
    # printf and the pipeline reads as "no match".
    if grep -Eq '(^|/)(Cargo\.toml|rust-toolchain(\.toml)?)$|\.rs$' <<<"$files"; then
        _proven_tool rustc rustc -V
        _proven_tool cargo cargo -V
    fi
    if grep -Eq '\.py$|(^|/)pyproject\.toml$' <<<"$files"; then
        _proven_tool python3 python3 -V
    fi
    if grep -Eq '\.swift$|(^|/)Package\.swift$|\.xcodeproj/' <<<"$files"; then
        _proven_tool swift swift --version
    fi
}

# The half read fresh on every key: the environment and the ignored files a build reads.
_proven_identity_live() {
    local name
    for name in $PROVEN_ENV_DEFAULT ${GOH_PROVEN_ENV:-}; do
        case "$name" in ""|[0-9]*|*[!A-Za-z0-9_]*) continue ;; esac
        if [ -n "${!name+set}" ]; then
            printf 'env %s=%s\n' "$name" "${!name}"
        else
            printf 'env %s unset\n' "$name"
        fi
    done
    # Ignored files the build reads (push_gate.sh carries the same list into its export). The top
    # level only when there is a list: it was a git spawn per key for the empty one, the usual case.
    [ -n "${GOH_EXPORT_KEEP:-}" ] || return 0
    local f top
    top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 0
    for f in ${GOH_EXPORT_KEEP:-}; do
        if [ -e "$top/$f" ]; then
            find "$top/$f" -type f -print 2>/dev/null | LC_ALL=C sort | while IFS= read -r p; do
                printf 'keep %s %s\n' "${p#"$top"/}" "$(hash_hex "$p")"
            done
        else
            printf 'keep %s absent\n' "$f"
        fi
    done
}

# proven_key <step> — prints "<key> <tree>", or returns 1 when there is no key: the cache is
# off, this is no git repo, or the working tree is not a tree git can name.
proven_key() {
    local tree key
    [ "${PROVEN_ON:-1}" = 1 ] || return 1
    tree="$(proven_tree)" && [ -n "$tree" ] || return 1
    key="$({ printf 'proven v1\ntree %s\nstep %s\n' "$tree" "$1"; proven_identity; } \
        | hash_hex /dev/stdin)" && [ -n "$key" ] || return 1
    printf '%s %s\n' "$key" "$tree"
}

# proven_scoped_key <step> <entry>... — like proven_key, but the tree part is the git OBJECT of
# each repo-relative entry (`.` is the whole tree) rather than the whole tree, so a step whose
# inputs are named keys only on them (BACKLOG P3; `goh rust-scope` names a crate's). An entry that
# does not exist is keyed as `missing`, so creating one changes the key. Same clean-tree
# precondition and identity as proven_key; one `cat-file --batch-check` for every entry.
proven_scoped_key() {
    local step="$1" tree objs key e; shift
    [ "${PROVEN_ON:-1}" = 1 ] || return 1
    [ "$#" -gt 0 ] || return 1
    tree="$(proven_tree)" && [ -n "$tree" ] || return 1
    objs="$(for e in "$@"; do
                if [ "$e" = . ]; then printf '%s\n' "$tree"; else printf '%s:%s\n' "$tree" "$e"; fi
            done | git cat-file --batch-check='%(objectname) %(objecttype)' 2>/dev/null \
            | sed 's/^.* missing$/missing/')" || return 1
    # (cat-file answers a missing entry by ECHOING "<tree>:<path> missing", and the whole tree's
    # hash in that line would key every scope on the whole tree again.)
    key="$({ printf 'proven v1 scoped\nstep %s\n' "$step"; printf 'scope %s\n' "$@"
             printf '%s\n' "$objs"; proven_identity; } | hash_hex /dev/stdin)" && [ -n "$key" ] \
        || return 1
    printf '%s %s\n' "$key" "$tree"
}

# proven_dir — where records live: the COMMON git dir, so every worktree of a repo (the push
# gate's throwaway export included) sees what the main checkout proved.
proven_dir() {
    local common
    common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
    printf '%s/goh-proven\n' "$common"
}

# proven_lookup <key> <step> — 0 when a record for this key is younger than the TTL and was made
# for this exact step string; prints "<age> <label>" (age human-readable, label the recorder's).
proven_lookup() {
    local dir f epoch="" by="" step="" line now age
    dir="$(proven_dir)" || return 1
    f="$dir/$1"
    [ -f "$f" ] || return 1
    while IFS= read -r line; do
        case "$line" in
            epoch=*) epoch="${line#epoch=}" ;;
            label=*) by="${line#label=}" ;;
            step=*)  step="${line#step=}" ;;
        esac
    done <"$f"
    case "$epoch" in ""|*[!0-9]*) return 1 ;; esac
    [ "$step" = "$2" ] || return 1
    now="$(proven_now)"
    age=$((now - epoch))
    [ "$age" -ge 0 ] && [ "$age" -lt "$PROVEN_TTL" ] || return 1
    printf '%s %s\n' "$(proven_age "$age")" "${by:-unknown}"
    return 0
}

# proven_age <seconds> — 42s, 7m, 3h05m.
proven_age() {
    local s="$1"
    if [ "$s" -lt 60 ]; then printf '%ss\n' "$s"
    elif [ "$s" -lt 3600 ]; then printf '%sm\n' "$((s / 60))"
    else printf '%sh%02dm\n' "$((s / 3600))" "$(((s % 3600) / 60))"
    fi
}

# proven_record <key> <tree> <step> <label> — write the record atomically, then prune expired ones.
proven_record() {
    local dir tmp now f epoch line
    dir="$(proven_dir)" || return 1
    mkdir -p "$dir" || return 1
    now="$(proven_now)"
    tmp="$(mktemp "$dir/.tmp.XXXXXX")" || return 1
    if ! { printf 'epoch=%s\nlabel=%s\ntree=%s\nstep=%s\n' "$now" "$4" "$2" "$3" >"$tmp" \
            && mv -f "$tmp" "$dir/$1"; }; then
        rm -f "$tmp"
        return 1
    fi
    for f in "$dir"/*; do
        [ -f "$f" ] || continue
        epoch=""
        IFS= read -r line <"$f" || true
        case "$line" in epoch=*) epoch="${line#epoch=}" ;; esac
        case "$epoch" in ""|*[!0-9]*) rm -f "$f"; continue ;; esac
        [ $((now - epoch)) -lt "$PROVEN_TTL" ] || rm -f "$f"
    done
    return 0
}
