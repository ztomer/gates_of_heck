# shellcheck shell=bash
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# _proven_identity.sh — the proven key's identity, sourced by gates/_proven.sh (split out of it at
# the 500-line cap, GOH.PREPARITY); its callers provide PROVEN_GOH_ROOT, _PROVEN_IDENTITY_PY,
# PROVEN_ENV_DEFAULT, GOH_PY, hash_hex and _proven_closure_on. The run-constant half (the gates
# roots by content, the toolchains) is asked once per gate; the live half (env, kept files) per key.

# _proven_tool <name> <cmd...> — one toolchain line, stdout's (stderr's when stdout is silent: GOH.PREPARITY)
_proven_tool() {
    local name="$1" line; shift
    if command -v "$name" >/dev/null 2>&1; then
        line="$("$@" 2>/dev/null </dev/null)"   # a rustup notice on stderr keyed one tree apart
        printf 'tool %s %s\n' "$name" "$(printf '%s\n' "${line:-$("$@" 2>&1 </dev/null)}" | head -n 1)"
    else
        printf 'tool %s absent\n' "$name"
    fi
}

# The run-constant half of proven_identity: the gates checkout's identity and the toolchains.
# ~110 ms, and every step's key was computed twice in every group of every gate (a private server
# workspace push: ~174 times). `proven_settings` asks it ONCE and exports it for the same working
# directory, PATH, toolchain override, gates root, GOH_LIVE, PYTHONPATH and GOH_PROVEN_CLOSURE;
# a key computed anywhere else asks again (tests/test_proven_identity_once.py).
_proven_memo_key() {
    printf '%s|%s|%s|%s|%s|%s|%s|%s' "$PWD" "$PATH" "${RUSTUP_TOOLCHAIN:-}" "${PROVEN_GOH_ROOT:-}" \
        "${GOH_DIR:-}" "${GOH_LIVE:-}" "${PYTHONPATH-<unset>}" "${GOH_PROVEN_CLOSURE:-off}"
}

proven_identity_prime() {
    [ "${PROVEN_ON:-1}" = 1 ] || return 0
    # Only where the export reaches the steps: local_ci.sh's validating `$(proven_settings)` is not.
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

# _proven_goh_roots — the gates checkouts the identity names, canonical, one per line, once each.
_proven_goh_roots() {
    local d seen=""
    for d in "${PROVEN_GOH_ROOT:-}" "${GOH_DIR:-}"; do
        [ -n "$d" ] && [ -d "$d" ] || continue
        d="$(cd "$d" && /bin/pwd -P)"
        case " $seen " in *" $d "*) continue ;; esac
        seen="$seen $d"
        printf '%s\n' "$d"
    done
}

# proven_keyed_roots — the paths whose CONTENT the identity carries, `:`-separated: every gates
# root's gates and the goh binary's build inputs (lib/gate_identity.py PARTS), read by bytes in a
# checkout, a head export or a plain directory alike. `goh rust-scope` leaves a path dependency
# inside one to the identity (GOH_PROVEN_KEYED_ROOTS; antiknob's `../gates_of_heck/crates/goh-tui`,
# R3.13); one elsewhere in a gates root (its docs/, its tests/) is not keyed, so not named.
proven_keyed_roots() {
    local d roots=() out
    while IFS= read -r d; do
        [ -n "$d" ] && roots+=(--root "$d")
    done < <(_proven_goh_roots)
    [ "${#roots[@]}" -gt 0 ] || { printf '\n'; return 0; }
    out="$("$GOH_PY" -S "$_PROVEN_IDENTITY_PY" keyed ${roots[@]+"${roots[@]}"} </dev/null 2>/dev/null)" || out=""
    printf '%s\n' "$(paste -sd: - <<<"$out")"
}

_proven_identity_slow() {
    local d files top args=()
    # The gates roots by CONTENT, the one formula lib/gate_identity.py keys a land by: a checkout and
    # its commit's head export hold the same bytes and share their proven steps (GOH.PROVENIDENTITY;
    # this file keyed a checkout by HEAD and diff, an export by its path). PYTHONPATH the same way,
    # each gates root on it by the gates' content (gate_identity.pythonpath, the formula the Rust
    # coverage key uses): keyed by path, the head export goh's land puts first on it, a new path per
    # commit, made every key new on every land (GOH.PROVENPYPATH). An argument, not the environment:
    # gate_identity.py re-runs HEAD's copy, which rewrites the variable. Then every tool a step may
    # invoke, by its bytes (GOH.VERDICTKEY). Unreadable: a line no other run has, so no hit.
    while IFS= read -r d; do
        [ -n "$d" ] && args+=(--root "$d")
    done < <(_proven_goh_roots)
    top="$(git rev-parse --show-toplevel 2>/dev/null)" || top="$PWD"
    args+=(--repo "$top")
    [ -z "${PYTHONPATH+set}" ] || args+=(--pythonpath "$PYTHONPATH")
    ! _proven_closure_on || args+=(--closure)   # off: the roots' content lines (GOH.PROVENCLOSURE2)
    "$GOH_PY" -S "$_PROVEN_IDENTITY_PY" proven "${args[@]}" </dev/null 2>/dev/null \
        || printf 'goh unreadable %s\n' "$$"
    # Toolchains for the languages the repo tracks (`swift --version` alone ~0.7 s: no Swift, no cost).
    files="$(git ls-files 2>/dev/null)"
    # Here-strings, not `printf | grep -q`: under pipefail an early grep exit reads as "no match".
    if grep -Eq '(^|/)(Cargo\.toml|rust-toolchain(\.toml)?)$|\.rs$' <<<"$files"; then
        _proven_tool rustc rustc -V
        _proven_tool cargo cargo -V
    fi
    if grep -Eq '\.py$|(^|/)pyproject\.toml$' <<<"$files"; then
        _proven_tool python3 "$GOH_PY" -V
    fi
    if grep -Eq '\.swift$|(^|/)Package\.swift$|\.xcodeproj/' <<<"$files"; then
        _proven_tool swift swift --version
    fi
}

# The half read fresh on every key: the environment and the ignored files a build reads.
# _proven_keep_lines <top> <path> -- `keep <path under top> <sha256>` for every regular file under
# <path>, sorted by path, from ONE hashing process per batch of arguments (GOH.RUSTPROVENKEY). It was
# a `$(hash_hex "$p")` per file -- a subshell, `shasum` and `cut` -- and a kept `.venv` of 3,037
# files cost 72 s per call at load 78; a group asks for it three or four times, which was the 107,
# 148 and 151 s silent gaps of game_asset_factory's push gate (2026-11-15). Where no batch hasher
# exists (a `cksum -a` only host) the per-file loop is the fallback, with the same lines.
_proven_keep_lines() {
    local top="$1" dir="$2" p hasher=()
    if command -v shasum >/dev/null 2>&1; then hasher=(shasum -a 256)
    elif command -v sha256sum >/dev/null 2>&1; then hasher=(sha256sum)
    fi
    if [ "${#hasher[@]}" -eq 0 ]; then
        find "$dir" -type f -print 2>/dev/null | env LC_ALL=C sort | while IFS= read -r p; do
            printf 'keep %s %s\n' "${p#"$top"/}" "$(hash_hex "$p")"
        done
        return 0
    fi
    # "<64 hex><space><space or *><path>": the path starts at column 67.
    find "$dir" -type f -print0 2>/dev/null | env LC_ALL=C sort -z \
        | xargs -0 "${hasher[@]}" 2>/dev/null \
        | env LC_ALL=C awk -v top="$top/" '{ p = substr($0, 67); if (index(p, top) == 1) p = substr(p, length(top) + 1)
            print "keep " p " " substr($0, 1, 64) }'
    return 0   # an unreadable file is a missing line, never a failed key (pipefail callers)
}

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
            _proven_keep_lines "$top" "$top/$f"
        else
            printf 'keep %s absent\n' "$f"
        fi
    done
}
