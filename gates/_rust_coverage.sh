# shellcheck shell=bash disable=SC2154  # cargo_dir, HERE, _change_base: rust_gate.sh's, set before this is sourced
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
#
# `rust_gate.sh`'s coverage step, sourced (split for the line cap, GOH.COVOFFLAND):
# `rust_coverage_checks` (the instrumented measurement and its floors through `coverage_gate.sh`),
# `rust_coverage_plain` (its tests plain: cargo's targets and the external suite against the plain
# build), `rust_coverage_measured_or_plain` (the reuse between them, `_rust_proven.sh`), and
# `rust_coverage_plain_only`, a land's `GOH_RUST_COVERAGE=plain` run: the tests plain, nothing
# measured, the floors the ship's (`lib/ship_full_run.py`). Pinned by `test_rust_gate.py`.
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_rust_coverage.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/pid_owner.sh
. "$_goh_d/../lib/pid_owner.sh" || exit 1
# gates/_rust_coverage.sh -- rust_gate.sh's coverage step: the measurement (rust_coverage_checks),
# its tests plain (rust_coverage_plain), the reuse between them (gates/_rust_proven.sh), and a land's
# plain run (rust_coverage_plain_only). Sourced by gates/rust_gate.sh, which runs one of them by
# GOH_RUST_COVERAGE; split out for its line cap (GOH.COVOFFLAND). Pinned by tests/test_rust_gate.py.

rust_coverage_checks() {
    if [ -n "${GOH_COV_FLOOR_RUST:-}" ]; then
        goh_step "coverage (floor ${GOH_COV_FLOOR_RUST}%)" \
            ${_change_base[@]+"${_change_base[@]}"} bash "$HERE/coverage_gate.sh" --lang rust \
            ${GOH_COV_RUST_EXTERNAL:+--external "$GOH_COV_RUST_EXTERNAL"} \
            ${GOH_COV_FLOORS_JSON:+--floors-json "$GOH_COV_FLOORS_JSON"} \
            --floor "$GOH_COV_FLOOR_RUST" "$cargo_dir"
    else
        goh_step "coverage (per-target floors)" \
            ${_change_base[@]+"${_change_base[@]}"} bash "$HERE/coverage_gate.sh" --lang rust \
            ${GOH_COV_RUST_EXTERNAL:+--external "$GOH_COV_RUST_EXTERNAL"} \
            --floors-json "$GOH_COV_FLOORS_JSON" "$cargo_dir"
    fi
}

# The tests of a coverage run, without the measurement: cargo's own targets (lib, bins, tests;
# doctests are a step of their own where a repo wants them) and the external suite against the
# plain build of the same Rust inputs. Run only when that measurement is recorded (GOH.RUSTCOVSKIP).
rust_coverage_plain() { rust_cov_cargo_plain && rust_cov_external_plain; }
_rust_cov_unbind() {
    local v
    _unbind=(-u LLVM_PROFILE_FILE)
    for v in $(git rev-parse --local-env-vars 2>/dev/null); do _unbind+=(-u "$v"); done
    goh_timings_unbind _unbind  # the tests write no row into this gate's profile (GOH.TIMINGSBLEED)
}
rust_cov_cargo_plain() {
    local _unbind
    _rust_cov_unbind
    goh_step_in "$cargo_dir" "cargo test (plain)" \
        env "${_unbind[@]}" cargo test --locked --workspace --tests --all-features
}
rust_cov_external_plain() {
    local _unbind
    [ -n "${GOH_COV_RUST_EXTERNAL:-}" ] || return 0   # GOH_RUST_COVERAGE=plain with no external suite
    _rust_cov_unbind
    # shellcheck disable=SC2154  # RUST_COV_PLAIN_BINS: set by rust_coverage_reuse or _plain_only
    goh_step_in "$cargo_dir" "external (plain): $GOH_COV_RUST_EXTERNAL" \
        env "${_unbind[@]}" GOH_COVERAGE_BIN_DIR="$RUST_COV_PLAIN_BINS" bash -c "$GOH_COV_RUST_EXTERNAL"
}
rust_coverage_measured_or_plain() { rust_coverage_reuse rust_coverage_checks rust_coverage_plain; }

# GOH_RUST_COVERAGE=plain (GOH.COVOFFLAND): a LAND of a repo whose ship makes the instrumented full
# run (GOH_SHIP_FULL_SUITE=on), which judges every floor and the diff coverage of everything landed
# since the last ship and bisects a red one to its land (lib/ship_full_run.py). The instrumented run
# was 69-76% of a goh land's gate (493-781 s); here the tests run plain, so a red test is still red.
# The external suite drives the plain build of THIS tree: none is a refusal, never a measurement.
# Two groups (GOH.GATEFLOOR): cargo's own tests keyed on what a Rust test reads (gates/_rust_inputs.sh
# `tests`: the tree minus GOH_RUST_TEST_UNREAD), so a land of the Python suite alone skips its
# 41-69 s; the external suite reads the whole tree, keyed on it as before.
# THE TWO RUN AT ONCE (GOH.RUSTOVERLAP): _rust_cov_at_once, below.
rust_coverage_plain_only() {
    measure "[rust] coverage: not measured at a land (GOH_RUST_COVERAGE=plain) -- the ship measures every floor over what landed since the last ship; the tests run plain"
    if [ -z "${GOH_COV_RUST_EXTERNAL:-}" ]; then
        rust_proven_group cargo-test tests rust_cov_cargo_plain
        return
    fi
    RUST_COV_PLAIN_BINS="$(rust_cov_plain_bins)" \
        || die "[rust] GOH_RUST_COVERAGE=plain: no plain build of this tree for the external suite: $RUST_COV_PLAIN_BINS"
    _rust_cov_at_once
}

# ── CARGO'S TESTS BESIDE THE EXTERNAL SUITE (GOH.RUSTOVERLAP) ────────────────────────────────────
# They ran one after the other, cargo first: 43 s p50 of a land's gate while the suite, seven times
# its CPU, had not started (213 runs, 10,849 s in 7 days). Neither reads what the other writes.
#   * Each is a PART: its whole proven group (key, steps, record) in a subshell with its own step log.
#   * ITS SHARE: the suite's is the gate's, live as before; cargo's tests have half of it until the
#     suite ends (its share file goes: lib/worker_share.py `part_of`). The sum is over the gate's
#     share on purpose, by measurement: cargo's tests wait more than they compute (1.0-2.3 busy cores
#     at 2 to 16 threads), and a suite held to 3/4 gave back 16 s of the 35 won (share 8, load 45).
#   * READABLE: a part's output is printed whole when the part ends, never interleaved.
#   * THE FIRST RED STOPS THE OTHER and is the red named, by its step's own line. A part that had
#     already ended red beside it is shown as well. INT, TERM and HUP stop both.
_rust_part_cargo() { goh_workers_export --split 2 && rust_proven_group cargo-test tests rust_cov_cargo_plain; }
_rust_part_external() { rust_proven_group coverage tree rust_cov_external_plain; }
_rust_part_stamps=()

# _rust_part_stop <stamp> -- the part, its step's wrapper and everything below them, gone. STOPPED
# first: with its step killed a running part would go on to the next one (a key that fails RUNS the
# group) before its own TERM came. The TERM is the wrapper's (goh step), first and alone: it sweeps
# its step's whole session, a KILL after GOH_STEP_GRACE. With the step TERMed first (a tree kill) the
# wrapper saw a step that had ended and left what it had in the background running.
_rust_part_stop() {
    local stamp="${1:-}" kid s left="" kids until
    kids=()
    goh_pid_kill STOP "$stamp" || return 0   # already ended
    while read -r kid; do
        ! s="$(goh_pid_stamp "$kid")" || kids[${#kids[@]}]="$s"
    done < <(pgrep -P "${stamp%% *}" || true)
    for s in ${kids[@]+"${kids[@]}"}; do goh_pid_kill TERM "$s" || true; done
    until=$((SECONDS + ${GOH_STEP_GRACE:-5} + 25))
    while [ "$SECONDS" -lt "$until" ]; do
        left=""
        for s in ${kids[@]+"${kids[@]}"}; do ! goh_pid_alive "$s" || left="$s"; done
        [ -n "$left" ] || break
        sleep 0.1
    done
    [ -z "$left" ] || warn "[rust] pid ${left%% *} did not end on TERM -- killed"
    goh_pid_tree_kill KILL "$stamp" || true
    wait "${stamp%% *}" 2>/dev/null || true
}

_rust_parts_interrupted() { # <signal> <exit code>
    local s
    trap - TERM INT HUP
    for s in ${_rust_part_stamps[@]+"${_rust_part_stamps[@]}"}; do _rust_part_stop "$s"; done
    err "$GOH_NAME: stopped by $1 -- cargo's tests and the external suite were stopped with it"
    exit "$2"
}

_rust_cov_at_once() {
    local dir i o got rc name=("cargo test (plain)" "external (plain)") pid=() ended=("" "")
    local part=(_rust_part_cargo _rust_part_external)
    dir="$(mktemp -d "$(goh_tmp)/goh-rust-parts.XXXXXX")" || die "[rust] no temp dir for the two test parts"
    goh_cleanup_add "$dir"
    info "[rust] ${name[0]} beside ${name[1]}; each prints as it ends"
    printf '1/2\n' >"$dir/share"
    trap '_rust_parts_interrupted TERM 143' TERM
    trap '_rust_parts_interrupted INT 130' INT
    trap '_rust_parts_interrupted HUP 129' HUP
    for i in 0 1; do
        # shellcheck disable=SC2034  # GOH_LOG: goh_step's (gates/_common.sh); exported, it would move every key
        ( GOH_LOG="$dir/step.$i"
          [ "$i" = 1 ] || export GOH_SHARE_STEPS="${GOH_SHARE_STEPS:+$GOH_SHARE_STEPS:}$dir/share"
          "${part[i]}" || exit "$?" ) >"$dir/out.$i" 2>&1 </dev/null &
        pid[i]=$!
        _rust_part_stamps[i]="$(goh_pid_stamp "$!")" || _rust_part_stamps[i]=""   # already ended
    done
    while [ -z "${ended[0]}" ] || [ -z "${ended[1]}" ]; do
        got="" rc=0
        wait -n -p got "${pid[@]}" || rc=$?
        i=0; [ "${got:-}" != "${pid[1]:-}" ] || i=1
        o=$((1 - i))
        ended[i]=$rc
        unset "pid[$i]" "_rust_part_stamps[$i]"
        rm -f "$dir/share"
        if [ "$rc" -eq 0 ]; then
            cat "$dir/out.$i"
            continue
        fi
        if [ -z "${ended[o]}" ]; then
            if goh_pid_alive "${_rust_part_stamps[o]:-}"; then
                _rust_part_stop "${_rust_part_stamps[o]}"
                info "[rust] ${name[o]}: stopped, no verdict -- ${name[i]} went red first"
            else
                wait "${pid[o]}" || true
                cat "$dir/out.$o" >&2
            fi
        fi
        cat "$dir/out.$i" >&2
        err "[rust] ${name[i]} is red (exit $rc)"
        exit "$rc"
    done
    trap - TERM INT HUP
}
