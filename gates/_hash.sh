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
