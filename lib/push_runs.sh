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

# THE SIBLINGS. A path dependency that leaves the checkout resolves BESIDE it, and beside the
# export -- <export root>/<repo>-<hash>/ -- there is nothing but the tree. A Rust consumer's
# `../gates_of_heck/crates/goh-tui` (the house TUI, BACKLOG 4.14) died there on "no matching
# package named `goh-tui`" (antiknob, 2026-10-24), and the link its own gate.sh made as a
# workaround was then named as the gate writing outside its tree; token-pool's widget
# (`.package(path: "../../ZoneWM/Packages/SpaceBarKit")`, 2026-11-07) died the same way. So the
# export provides every sibling a TRACKED Cargo.toml or Package.swift names (gates/_push_siblings.py),
# as a link: gates_of_heck to $GOH, any other to the checkout of that name beside the real one, when
# there is one ($1 = the export's tree, $2 = the run directory, $3 = the real checkout). Each link
# is recorded in $2/.siblings so `run_litter` knows it for the run's own. An existing entry is never
# replaced; a sibling that is not checked out gets no link, and the build says what is missing.
export_siblings() {
    local tree="$1" run_dir="$2" root="$3" name target
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if [ -e "$run_dir/$name" ] || [ -L "$run_dir/$name" ]; then continue; fi
        if [ "$name" = gates_of_heck ]; then target="$GOH"; else target="$(dirname "$root")/$name"; fi
        [ -d "$target" ] || continue
        ln -s "$target" "$run_dir/$name"
        printf '%s\t%s\n' "$name" "$target" >>"$run_dir/.siblings"
        info "pre-push: the export carries the sibling $name (a tracked manifest's path dependency names it)"
    done < <("$GOH_PY" -S "$GOH/gates/_push_siblings.py" "$tree")
}

# private_cargo_config RUN_DIR BUILD_DIR: the private run's cargo config (see push_gate.sh): redirects the
# user-global, path-keyed build-dir into the run. The kit's own entry, so it is RECORDED for run_litter.
private_cargo_config() {
    local esc="${2//\\/\\\\}"
    mkdir -p "$1/.cargo"
    printf '[build]\nbuild-dir = "%s"\n' "${esc//\"/\\\"}" >"$1/.cargo/config.toml"
    printf '.cargo\n' >>"$1/.kit-wrote"
}

# What a gate left in run directory $1 beyond the run's own entries: the owner stamp, the private
# build dir, the tree ($2), the sibling links export_siblings made (and recorded), and every name the
# kit RECORDED in `.kit-wrote` as its own (the private run's `.cargo/config.toml` was reported to
# ztools as its gate's litter, 2026-11-08). NAMED, not swept silently -- the defect is the consumer's.
# Any other thing under that name is still named: the exemption is the record, not the name.
run_litter() {
    local run_dir="$1" worktree="$2" name out=""
    while IFS= read -r name; do
        case "$name" in
            .owner | .cargo-build | .siblings | .kit-wrote | "$(basename "$worktree")") continue ;;
        esac
        if [ -f "$run_dir/.kit-wrote" ] && grep -qxF "$name" "$run_dir/.kit-wrote"; then continue; fi
        if [ -L "$run_dir/$name" ] && [ -f "$run_dir/.siblings" ] &&
            grep -qxF "$name"$'\t'"$(readlink "$run_dir/$name")" "$run_dir/.siblings"; then
            continue
        fi
        out="$out$name "
    done < <(ls -A "$run_dir")
    printf '%s' "$out"
}

