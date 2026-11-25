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
rust_coverage_plain_only() {
    measure "[rust] coverage: not measured at a land (GOH_RUST_COVERAGE=plain) -- the ship measures every floor over what landed since the last ship; the tests run plain"
    rust_proven_group cargo-test tests rust_cov_cargo_plain || return
    [ -n "${GOH_COV_RUST_EXTERNAL:-}" ] || return 0
    RUST_COV_PLAIN_BINS="$(rust_cov_plain_bins)" \
        || die "[rust] GOH_RUST_COVERAGE=plain: no plain build of this tree for the external suite: $RUST_COV_PLAIN_BINS"
    rust_proven_group coverage tree rust_cov_external_plain
}
