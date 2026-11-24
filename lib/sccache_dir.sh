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
# The cap is the config file's alone, written by lib/sccache_cap.py (GOH.SCCACHECAP): an inherited
# SCCACHE_CACHE_SIZE outranks it, so a server a gate starts would take a hand-set second value.
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/sccache_dir.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
if [ -z "${SCCACHE_DIR:-}" ]; then
    case "$(uname -s)" in
        Darwin) SCCACHE_DIR="$HOME/Library/Caches/Mozilla.sccache" ;;
        *) SCCACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/sccache" ;;
    esac
    export SCCACHE_DIR
    export GOH_SCCACHE_PINNED="$SCCACHE_DIR"   # goh's word, not the user's choice: lib/goh-rustc reads it
fi
unset SCCACHE_CACHE_SIZE
# The pin is only the fallback: a missing server is started HERE, on the resolver's root.
"${BASH_SOURCE[0]%/*}/goh-rustc" --goh-server >&2 || :
