# shellcheck shell=bash
# The push gate's run ownership: who holds a run dir under `$export_root`, and reaping what a killed
# gate left behind. Sourced by gates/push_gate.sh after the tui lib (for `info`).

# `<pid> <start time>`: the identity of a live process. A pid alone is reused; the pair is not.
owner_stamp() {
    printf '%s %s\n' "$1" "$(ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//')"
}

# Reap what killed runs left under export root $1: a run directory whose owner is not alive. Only directories carrying an
# `.owner` are judged; anything else in the root is not this layout's to delete.
reap_dead_runs() {
    local export_root="$1" dir pid
    for dir in "$export_root"/*/; do
        [ -f "$dir.owner" ] || continue
        read -r pid _ <"$dir.owner" || continue
        if [ "$(owner_stamp "$pid")" = "$(cat "$dir.owner")" ] && kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        info "pre-push: removing a run left by a killed gate: ${dir%/}"
        rm -rf "$dir"
    done
    # A stable export's `.source` marker outlives its runs by design, and dies with its checkout.
    local marker owner
    for marker in "$export_root"/*.source; do
        [ -f "$marker" ] || continue
        read -r owner <"$marker" || owner=""
        [ -n "$owner" ] && [ -e "$owner" ] || rm -f "$marker"
    done
}

# THE gates_of_heck SIBLING. A Rust consumer whose Cargo.toml has a path dependency on
# ../gates_of_heck/crates/goh-tui (the house TUI, BACKLOG 4.14) resolves it BESIDE its checkout, and
# beside the export -- <export root>/<repo>-<hash>/ -- there is nothing but the tree. Cargo reads no
# environment variable in a path, so antiknob's push died on "no matching package named `goh-tui`"
# (2026-10-24) before its own gate.sh could act, and the link its gate.sh made as a workaround was
# then named here as the gate writing outside its tree. So the export provides the sibling itself,
# as a link to $GOH, for exactly the consumers whose TRACKED Cargo.toml names it ($1 = the export's
# tree, $2 = the run directory). An existing entry is never replaced.
export_goh_sibling() {
    local tree="$1" run_dir="$2"
    if [ -e "$run_dir/gates_of_heck" ] || [ -L "$run_dir/gates_of_heck" ]; then return 0; fi
    git -C "$tree" grep -qEI -e 'path[[:space:]]*=[[:space:]]*"(\.\./)+gates_of_heck(/|")' \
        -- ':(glob)**/Cargo.toml' || return 0
    ln -s "$GOH" "$run_dir/gates_of_heck"
    info "pre-push: the export carries a gates_of_heck sibling (a Cargo.toml path dependency names it)"
}

# What a gate left in run directory $1 beyond the run's own entries: the owner stamp, the private
# build dir, the tree ($2), and the gates_of_heck link export_goh_sibling made. NAMED, not swept
# silently -- the defect is the consumer's. Any other thing under that name is still named.
run_litter() {
    local run_dir="$1" worktree="$2" name out=""
    while IFS= read -r name; do
        case "$name" in
            .owner | .cargo-build | "$(basename "$worktree")") continue ;;
            gates_of_heck) [ "$(readlink "$run_dir/$name")" = "$GOH" ] && continue ;;
        esac
        out="$out$name "
    done < <(ls -A "$run_dir")
    printf '%s' "$out"
}

# THIS suite never writes the user's push logs or worktrees (G11, 2026-10-26: a test ran the push
# gate with its GOH_* scrubbed and left a zero-byte `repo-<sha>-<time>.log` in the real log root at
# every suite run). Under goh's own pytest (the marker is not GOH_-prefixed, so no env scrub drops
# it) a run that names no private roots is refused, naming the test, before it writes anything.
push_roots_private_under_suite() {
    if [ -n "${PYTEST_CURRENT_TEST:-}" ] && [ -n "${GATES_OF_HECK_SUITE:-}" ] \
        && { [ -z "${GOH_PUSH_LOGS:-}" ] || [ -z "${GOH_PUSH_WORKTREES:-}" ]; }; then
        die "pre-push: started by goh's suite ($PYTEST_CURRENT_TEST) with no GOH_PUSH_LOGS/GOH_PUSH_WORKTREES -- it would write the real ~/.cache/goh; keep them (tests/host_locks.py)"
    fi
}
