# shellcheck shell=bash
# _proven_artifacts.sh -- the ARTIFACTS a proven step declares, and the builds that declare none.
# Sourced by gates/_proven.sh (split out of it at the 500-line cap); the cache's key, lookup and
# record live there, and call these. Pure bash, no spawn except the artifact hash, bash 3.2 safe.

# ── THE ARTIFACTS A STEP DECLARES ───────────────────────────────────────────────────────────────
#
# THE CLASS (divoom-control, 2026-10-20). The cache remembered "this step exited 0" and read that
# as "the artifact a later step needs is there". The pre-push gate runs in a FRESH worktree: the
# build step was proven on the same commit by an earlier push attempt, so it was SKIPPED, so it
# left no binary behind, and the step that consumes it failed with "binary not found" -- a reason
# that has nothing to do with the tree.
#
# THE DECLARATION rides in the step string's bracket prefix, so it travels with the step and cannot
# drift from it:
#
#     [produces=target/debug/divoomd] cargo build -p divoomd --locked
#     [consumes=target/debug/divoomd] python3 tools/check_built_binaries.py
#
# `produces=` is what the step CREATES; `consumes=` is what it READS. Several paths are separated
# by `+` (a comma separates the items in the prefix). Paths are repo-relative.
#
# THE RULES. A record may only be written when every declared artifact is THERE (a build that
# "succeeded" and produced nothing is not a proof of anything), and a hit is only a hit while
# every declared artifact is still there, UNCHANGED (a missing one is the fresh worktree; a
# changed one is a different build). The declaration is part of the step string, so it is part of
# the key: a record written before the declaration existed can never be found.

# proven_decl <step> — the declared artifacts, one per line: "<produces|consumes> <path>".
# Pure parameter expansion: no spawn, and bash 3.2 safe.
proven_decl() {
    local step="$1" head rest item kind paths path
    case "$step" in
        "["*"] "*) head="${step%%]*}" ;;
        *) return 0 ;;
    esac
    head="${head#[}"
    rest="$head"
    while [ -n "$rest" ]; do
        item="${rest%%,*}"
        if [ "$item" = "$rest" ]; then rest=""; else rest="${rest#*,}"; fi
        case "$item" in
            produces=*|consumes=*) ;;
            *) continue ;;
        esac
        kind="${item%%=*}"
        paths="${item#*=}"
        while [ -n "$paths" ]; do
            path="${paths%%+*}"
            if [ "$path" = "$paths" ]; then paths=""; else paths="${paths#*+}"; fi
            [ -n "$path" ] && printf '%s %s\n' "$kind" "$path"
        done
    done
}

# proven_decl_valid <step> — 0 when every declaration in the prefix is one of the two kinds.
# An unknown key is REFUSED, not ignored: a typo in a declaration is a gate that silently checks
# nothing, which is the class this file exists for.
proven_decl_valid() {
    local step="$1" head rest item kind paths path
    case "$step" in
        "["*"] "*) head="${step%%]*}" ;;
        *) return 0 ;;
    esac
    head="${head#[}"
    rest="$head"
    while [ -n "$rest" ]; do
        item="${rest%%,*}"
        if [ "$item" = "$rest" ]; then rest=""; else rest="${rest#*,}"; fi
        case "$item" in
            *=*) ;;
            *) continue ;;
        esac
        kind="${item%%=*}"
        case "$kind" in
            produces|consumes) ;;
            *) return 1 ;;
        esac
        paths="${item#*=}"
        while [ -n "$paths" ]; do
            path="${paths%%+*}"
            if [ "$path" = "$paths" ]; then paths=""; else paths="${paths#*+}"; fi
            case "$path" in
                ""|*" "*|*"$(printf '\t')"*) return 1 ;;
            esac
        done
    done
    return 0
}

# proven_artifact_hash <path> — the sha256 of a file's bytes; 1 when it cannot be read. The record
# and the lookup must agree on it, and the native half hashes the same way (sha256).
proven_artifact_hash() {
    local path="$1" out
    [ -f "$path" ] || return 1
    out="$(hash_hex "$path")" || return 1
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
}

# proven_artifacts_present <record file> — 0 when every artifact the proof was about is still
# there and unchanged. A MISSING one and a CHANGED one both void the proof.
proven_artifacts_present() {
    local f="$1" line kind rest path want got
    [ -f "$f" ] || return 1
    while IFS= read -r line; do
        case "$line" in
            "produces "*|"consumes "*) ;;
            *) continue ;;
        esac
        kind="${line%% *}"
        rest="${line#* }"
        path="${rest%% *}"
        want="${rest#* }"
        got="$(proven_artifact_hash "$path")" || return 1
        [ "$got" = "$want" ] || return 1
    done <"$f"
    return 0
}

# proven_undeclared_build <step> — 0 when the step is a build tool's BUILD and declares no
# `produces=`: such a step is never looked up (G1b(b), 2026-10-25). 7.2 made the cache honour the
# artifacts a step DECLARES, and the consumer it was found in never declared one (divoom-control's
# `cargo build -p divoomd --locked` still carried no `[produces=]`), so the class lived on behind a
# fix that needed remembering. A build's point is its artifact, and a verdict cache cannot know
# where that went (a push gate's fresh worktree has none), so the safe state is the default: an
# undeclared build always runs. Declaring `[produces=PATH]` makes it cacheable under 7.2's rules.
# The command is read as WORDS after leading `NAME=value` assignments (and `env`): the tool, then
# its build verb -- `cargo build|b` (a `+toolchain` skipped), `swift build`, `go build`,
# `xcodebuild ... build|build-for-testing`, `cmake --build`, `npm|pnpm|yarn [run] build`. A word
# that merely appears later (`echo cargo build`) is not a build. Pure bash: no spawn.
proven_undeclared_build() {
    local step="$1" decl cmd
    decl="$(proven_decl "$step")"
    case "$decl" in "produces "*|*$'\n'"produces "*) return 1 ;; esac
    cmd="$step"
    case "$cmd" in "["*"] "*) cmd="${cmd#*] }" ;; esac
    local -a w
    read -r -a w <<<"$cmd" || true
    local i=0 n="${#w[@]}"
    while [ "$i" -lt "$n" ]; do
        case "${w[i]}" in
            env) i=$((i + 1)) ;;
            [A-Za-z_]*=*) case "${w[i]%%=*}" in *[!A-Za-z0-9_]*) break ;; esac; i=$((i + 1)) ;;
            *) break ;;
        esac
    done
    [ "$i" -lt "$n" ] || return 1
    local tool="${w[i]##*/}" verb="${w[i + 1]:-}" j
    case "$tool" in
        cargo)
            case "$verb" in +*) verb="${w[i + 2]:-}" ;; esac
            case "$verb" in build|b) return 0 ;; esac ;;
        swift|go) [ "$verb" = build ] && return 0 ;;
        cmake) [ "$verb" = --build ] && return 0 ;;
        npm|pnpm|yarn)
            [ "$verb" = build ] && return 0
            [ "$verb" = run ] && [ "${w[i + 2]:-}" = build ] && return 0 ;;
        xcodebuild)
            for ((j = i + 1; j < n; j++)); do
                case "${w[j]}" in build|build-for-testing) return 0 ;; esac
            done ;;
    esac
    return 1
}
