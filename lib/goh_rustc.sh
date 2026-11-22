#!/bin/sh
# cargo's rustc-wrapper AHEAD of sccache (GOH.SCCACHEENV, design GOH.CACHEEVICT section 4, F1b).
# Cargo runs `<wrapper> <rustc> <args...>`; this drops the variables only CARGO reads, then hands the
# compile to sccache unchanged.
#
# sccache 0.19 hashes every CARGO_* variable rustc inherits into the Rust key. Cargo passes its own
# environment down, so a lane, an IDE or another repo that exports CARGO_TARGET_DIR, a
# CARGO_BUILD_* or CARGO_TERM_* gives every compile under it a key no other build shares (probe:
# CARGO_TERM_COLOR exported, 0 hits of 12 against a warm cache). rustc reads none of them: cargo
# turns each into flags before it runs rustc.
#
# Named deviation: a crate that reads one of these with `env!`/`option_env!` sees it unset. The
# variables cargo sets FOR crates (CARGO_PKG_*, CARGO_MANIFEST_*, CARGO_CRATE_NAME, CARGO, ...)
# are kept, and stay in the key.
#
# GOH_RUSTC_NEXT names what runs next (default: sccache on PATH).
unset CARGO_TARGET_DIR CARGO_HOME CARGO_INCREMENTAL
for v in $(env | awk -F= '/^CARGO_(BUILD|TERM)_[A-Za-z0-9_]*=/ { print $1 }'); do
    unset "$v"
done
exec "${GOH_RUSTC_NEXT:-sccache}" "$@"
