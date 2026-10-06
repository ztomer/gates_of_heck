# shellcheck shell=bash
# Sourced by install.sh and gates/doctor.sh: the ONE hash of an installed hook,
# so the record install writes and the comparison doctor makes cannot drift.
# sha256 digest of a file's contents, via whichever tool exists. All three
# produce the same SHA-256 digest, so records stay comparable across the chain.
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
# a key varied with where a gate was started (tests/test_verdict_free_keys.py).
goh_config_hash() {
    local free
    free="$(grep -v '^#' "$(dirname "${BASH_SOURCE[0]}")/verdict_free_keys.txt" | paste -sd'|' -)"
    env | grep '^GOH_' | grep -v -E "^(${free})=" | LC_ALL=C sort | hash_hex /dev/stdin
}
