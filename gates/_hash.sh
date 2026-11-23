# shellcheck shell=bash
# Sourced by install.sh and gates/doctor.sh: the ONE hash of an installed hook,
# so the record install writes and the comparison doctor makes cannot drift.
# sha256 digest of a file's contents, via whichever tool exists. All three
# produce the same SHA-256 digest, so records stay comparable across the chain.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_hash.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
hash_hex() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif cksum -a sha256 /dev/null >/dev/null 2>&1; then
        cksum -a sha256 "$1" | cut -d' ' -f1
    else
        return 1
    fi
}

# goh_config_hash -- the GOH_* configuration a verdict depends on, as one hash: every GOH_* in the
# environment except the keys that never change a verdict (gates/verdict_free_keys.txt, the one
# list). It held GOH_RESOLVED_* and GOH_PROVEN_IDENTITY_FOR (a $PWD) before that list existed, so
# a key varied with where a gate was started (tests/test_verdict_free_keys.py). goh_config_hash KEY...
# leaves those out too: a caller whose key carries their effect another way (by content) says so.
goh_config_hash() {
    local free k
    free="$(grep -v '^#' "$(dirname "${BASH_SOURCE[0]}")/verdict_free_keys.txt" | paste -sd'|' -)"
    for k in "$@"; do free="$free|$k"; done
    env | grep '^GOH_' | grep -v -E "^(${free})=" | env LC_ALL=C sort | hash_hex /dev/stdin
}
