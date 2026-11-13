# shellcheck shell=bash
# sccache_dir.sh -- sourced, never run. SOURCING IT pins SCCACHE_DIR to the user's compile cache.
#
# THE CLASS (GOH.CACHEBOUND, 2026-11-13). sccache keeps its cache under HOME when SCCACHE_DIR is
# unset, and a repo's test sandbox sets HOME (ztools: `rust/target/test-home`, a cargo runner). An
# sccache SERVER started from inside a sandboxed test inherits it, keeps it for its whole life, and
# serves every later build on the machine from a private cache nobody bounds or reads again: 16 GiB
# in one push export, 16 GiB more in the checkout's own target/, while the real cache sat idle.
# Pinned once, at the gate's entry, the sandbox can move HOME and nothing else: the variable is
# inherited by every child, so the server a test starts is the user's. A dir the user chose stays.
# Sourced by gates/_common.sh (every gate runner) and gates/push_gate.sh.
. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
if [ -z "${SCCACHE_DIR:-}" ]; then
    case "$(uname -s)" in
        Darwin) SCCACHE_DIR="$HOME/Library/Caches/Mozilla.sccache" ;;
        *) SCCACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/sccache" ;;
    esac
    export SCCACHE_DIR
fi
