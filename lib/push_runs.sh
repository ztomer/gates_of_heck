# shellcheck shell=bash
# The push gate's run ownership: who holds a run dir under `$export_root`, and reaping what a killed
# gate left behind. Sourced by gates/push_gate.sh after the tui lib (for `info`).

. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/rm_tree.sh"

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
        goh_rm_tree "$dir"
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
# at its HEAD: gates_of_heck from $GOH, any other from the checkout of that name beside the real one,
# when there is one ($1 = the export's tree, $2 = the run directory, $3 = the real checkout). Each
# is recorded in $2/.siblings (with the commit) so `run_litter` knows it for the run's own. An existing entry is never
# replaced; a sibling that is not checked out gets no link, and the build says what is missing.
export_siblings() {
    local tree="$1" run_dir="$2" root="$3" name target sha dirty
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if [ -e "$run_dir/$name" ] || [ -L "$run_dir/$name" ]; then continue; fi
        if [ "$name" = gates_of_heck ]; then target="$GOH"; else target="$(dirname "$root")/$name"; fi
        [ -d "$target" ] || continue
        # The sibling's HEAD, never its live working tree (GOH.PUSHSIBLINGS: ZeroThunder's push
        # judged game_asset_factory's uncommitted edit). A shared clone checked out at that commit:
        # nothing is written to the sibling's own .git, and the log names the commit linked.
        if sha="$(git -C "$target" rev-parse -q --verify 'HEAD^{commit}' 2>/dev/null)" &&
            [ "$(cd "$(git -C "$target" rev-parse --show-toplevel)" && /bin/pwd -P)" = "$(cd "$target" && /bin/pwd -P)" ]; then
            if ! { git clone -q --shared --no-checkout -c core.hooksPath=/dev/null "$target" "$run_dir/$name" &&
                git -C "$run_dir/$name" -c core.hooksPath=/dev/null checkout -q --detach "$sha"; } 2>/dev/null; then
                rm -rf "${run_dir:?}/$name"
                warn "pre-push: the sibling $name could not be checked out at ${sha:0:12}; the export does not carry it"
                continue
            fi
            printf '%s\t%s@%s\n' "$name" "$target" "$sha" >>"$run_dir/.siblings"
            dirty=""
            [ -z "$(git --no-optional-locks -C "$target" status --porcelain 2>/dev/null)" ] ||
                dirty="; its uncommitted edits are not gated"
            info "pre-push: the export carries the sibling $name at ${sha:0:12}, its HEAD (a tracked manifest's path dependency names it$dirty)"
        else
            # Not a git checkout (an installed snapshot such as goh's HEAD cache): no commit to name.
            ln -s "$target" "$run_dir/$name"
            printf '%s\t%s\n' "$name" "$target" >>"$run_dir/.siblings"
            warn "pre-push: the sibling $name ($target) is no git checkout, so it is linked as it stands"
        fi
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

# sibling_recorded RUN_DIR NAME: NAME is the sibling export_siblings made: a link to the target it
# recorded, or the checkout it cloned at the commit it recorded (and nothing else under that name).
sibling_recorded() {
    local dir="$1/$2" line
    line="$(grep -m1 -F "$2"$'\t' "$1/.siblings")" || return 1
    [ "${line%%$'\t'*}" = "$2" ] || return 1
    case "${line#*$'\t'}" in
        *@*) [ ! -L "$dir" ] && [ "$(git -C "$dir" rev-parse -q --verify HEAD 2>/dev/null)" = "${line##*@}" ] ;;
        *) [ -L "$dir" ] && [ "$(readlink "$dir")" = "${line#*$'\t'}" ] ;;
    esac
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
        if [ -f "$run_dir/.siblings" ] && sibling_recorded "$run_dir" "$name"; then continue; fi
        out="$out$name "
    done < <(ls -A "$run_dir")
    printf '%s' "$out"
}

# push_change_base ROOT COMMIT REMOTE_SHA REMOTE -- where the pushed change started, for the gate's
# GOH_CHANGE_BASE (GOH.CRATESDRIFT, docs/decisions/D-0001-*): the export is a detached worktree
# with no upstream, so a check that judges only what the change did (`goh crates-behind`) is told:
# the remote's tip of the ref, or for a new branch its merge base with the remote's default
# branch; empty when neither is known. The caller always sets it, so no inherited value stands in.
push_change_base() {
    if [ "$3" != "0000000000000000000000000000000000000000" ]; then
        git -C "$1" rev-parse --verify --quiet "$3^{commit}" || true
    elif [ -n "$4" ]; then
        git -C "$1" merge-base "$2" "refs/remotes/$4/HEAD" 2>/dev/null || true
    fi
}
