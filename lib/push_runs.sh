# shellcheck shell=bash
# The push gate's run ownership: who holds a run dir under `$export_root`, and reaping what a killed
# gate left behind. Sourced by gates/push_gate.sh after the tui lib (for `info`).

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "lib/push_runs.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# shellcheck source=lib/rm_tree.sh
. "${BASH_SOURCE[0]%/*}/rm_tree.sh"

# `<pid> <start time>`: the identity of a live process. A pid alone is reused; the pair is not.
owner_stamp() {
    printf '%s %s\n' "$1" "$(ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//')"
}

# Reap what killed runs left under export root $1: a run directory whose owner is not alive, or that
# has none and is past the grace below. Files in the root (the `.source` markers) are not judged here.
reap_dead_runs() {
    local export_root="$1" dir pid
    for dir in "$export_root"/*/; do
        case "$dir" in *.kept/) continue ;; esac   # a stable export's kept tree, never a run (push_export_tree)
        if [ ! -f "$dir.owner" ]; then
            # An ownerless run dir is a dead run's once it has idled past the gap between a run's
            # mkdir and its `.owner` (milliseconds): a stray writer (an sccache server a test
            # started, GOH.CACHEBOUND) recreated one, 16 GiB of it, and `mkdir` then failed on the
            # stable path for every push after -- "held by a live push", a cold private build.
            [ -n "$(find "$dir" -maxdepth 0 -mmin +10 2>/dev/null)" ] || continue
            info "pre-push: removing an ownerless run directory: ${dir%/}"
            goh_rm_tree "$dir"
            continue
        fi
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
        [ -n "$owner" ] && [ -e "$owner" ] && continue
        rm -f "$marker"
        [ ! -e "${marker%.source}.kept" ] || goh_rm_tree "${marker%.source}.kept"
    done
}

# push_export_tree ROOT TREE COMMIT KEPT: TREE, a detached worktree of ROOT at COMMIT (GOH.PUSHMTIME).
# A fresh `worktree add` gave every file a new mtime, so cargo rebuilt every workspace crate in
# every profile. KEPT is the last run's tree, set aside UNREGISTERED (push_keep_tree: no extra
# `git worktree list` entry): its files move into a --no-checkout worktree, the index is read and
# refreshed by content, and `reset --hard` rewrites only the files that differ, so the rest keep
# their mtimes and cargo rebuilds only what changed. `clean -ffdx` removes all a gate left (the
# in-tree target/ kept: cargo's own). Any step refused: a fresh tree, as before.
push_export_tree() {
    if [ -n "$4" ] && [ -d "$4" ] && git -C "$1" worktree add --detach --quiet --no-checkout "$2" "$3"; then
        local f ok=1
        for f in "$4"/* "$4"/.[!.]* "$4"/..?*; do
            [ -e "$f" ] || [ -L "$f" ] || continue
            [ "${f##*/}" = .git ] || mv "$f" "$2/" || ok=""
        done
        goh_rm_tree "$4"
        if [ -n "$ok" ] && git -C "$2" read-tree HEAD && { git -C "$2" update-index -q --refresh >/dev/null || :; } &&
            git -C "$2" reset -q --hard && git -C "$2" clean -ffdxq -e /target; then
            return 0
        fi
        git -C "$1" worktree remove --force "$2" >/dev/null 2>&1 || goh_rm_tree "$2"
        git -C "$1" worktree prune
    fi
    [ -z "$4" ] || [ ! -e "$4" ] || goh_rm_tree "$4"
    git -C "$1" worktree add --detach --quiet "$2" "$3"
}

# push_keep_tree ROOT TREE KEPT: TREE set aside at KEPT for the next run, mtimes kept, and no longer
# a worktree of ROOT (its .git link dropped, the entry pruned). Exit 1 leaves TREE for removal.
push_keep_tree() {
    [ ! -e "$3" ] || goh_rm_tree "$3"
    mv "$2" "$3" 2>/dev/null || return 1
    rm -f "$3/.git"
    git -C "$1" worktree prune 2>/dev/null || true
}

# THE SIBLINGS. A path dependency that leaves the checkout resolves BESIDE it, and beside the
# export -- <export root>/<repo>-<hash>/ -- there is nothing but the tree. A Rust consumer's
# `../gates_of_heck/crates/goh-tui` (the house TUI, BACKLOG 4.14) died there on "no matching
# package named `goh-tui`" (antiknob, 2026-10-24), and the link its own gate.sh made as a
# workaround was then named as the gate writing outside its tree; token-pool's widget
# (`.package(path: "../../app/Packages/SpaceBarKit")`, 2026-11-07) died the same way. So the
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

# THE RECORDED VERDICT (GOH.VERDICTAPI, GOH.ONEVERDICT): push_gate.sh calls this after every gated
# ref passed and none moved, with the repo and each commit it gated as `<sha>:<how>` (how empty: the
# full gate, else release or config). Each is recorded green in the one verdict store's `gate` path
# (lib/reuse.py), keyed by the repo, the commit's TREE and the gate identity of how, and in its
# `pushed` path by the tree alone (the floor rule's base, lib/config_push.py) -- on EVERY
# push, not only one `goh ship` runs: the per-ship file this wrote before (GOH_GATE_VERDICTS) never
# saw a commit the land queue pushed, so ship's own push had "nothing to gate" and the release kit
# re-ran the full gate (koffee_big, 3137.5 s). `goh ship verdict --sha` reads it
# (lib/ship_verdict.py); a reader never parses the gate's log text. A store that cannot be written
# is said, never fatal: a missing verdict costs a later re-gate, never a wrong skip.
push_record_verdicts() {
    local root="$1"
    shift
    [ "$#" -gt 0 ] || return 0
    "$GOH_PY" "${BASH_SOURCE[0]%/*}/reuse.py" record-gate --repo "$root" --by push -- "$@" \
        >/dev/null </dev/null \
        || warn "pre-push: the gate verdict was not recorded (lib/reuse.py) -- a ship re-gates these commits"
}

# THE FLOOR RULE (GOH.CFGPUSHSCOPE). push_config_base WORKTREE BASE prints BASE when
# `config_push.py decide` finds the pushed commit changes only scanner floors since a push-gated
# BASE (the remote's tip of the ref), else nothing, its reason said on stderr when the diff touched
# .gatesrc (a code push hears nothing of it). A decision that cannot
# run is the full gate, never a reduced one.
push_config_base() {
    local out
    out="$("$GOH_PY" "$GOH/lib/config_push.py" decide --root "$1" --base "$2" </dev/null)" \
        || out="full the floor rule could not decide (above)"
    case "$out" in
        "config "?*) printf '%s\n' "${out#config }" ;;
        "quiet "*) ;;   # a push this rule does not concern: the full gate, nothing said
        *) info "pre-push: the full gate: ${out#full }" >&2 ;;
    esac
}

# push_config_run BASE -- in the export: the structural gate alone, the one reader of a floor; BASE's
# green push verdict stands for every other step (push_config_base chose it).
push_config_run() {
    info "pre-push: scanner floors only since ${1:0:8} -- the structural gate, and ${1:0:8}'s green" \
        "push verdict for the rest (GOH.CFGPUSHSCOPE)"
    bash "$GOH/gates/structural.sh" --full
}

# export_kept ROOT WORKTREE PATH... -- copy each GOH_EXPORT_KEEP path ROOT holds into the export.
export_kept() {
    local root="$1" worktree="$2" f
    shift 2
    for f in "$@"; do
        if [ -e "$root/$f" ]; then
            mkdir -p "$worktree/$(dirname "$f")"
            cp -R "$root/$f" "$worktree/$f"
            info "carried ignored file into the export: $f"
        fi
    done
}

# push_prune_logs DIR KEEP -- a red push keeps its log; past KEEP logs the oldest go.
push_prune_logs() {
    find "$1" -name '*.log' -type f -print0 | xargs -0 -r ls -t | tail -n "+$(($2 + 1))" \
        | while IFS= read -r old; do rm -f "$old"; done
}

# THE RELEASE RULE (GOH.SHIPRELREUSE R3; lib/release_gate.py's header). push_release_base ROOT
# WORKTREE prints the parent whose land verdict the pushed commit reuses, or nothing: the repo must
# declare GOH_PUSH_RELEASE_REUSE=on (read by name, in a child), and `release_gate.py push-decide`
# must find the commit release files only on a land-green parent. Any other answer is the full gate,
# its reason said on stderr; a decision that cannot run is the full gate too, never a reduced one.
# The audit's sampled reuse prints `shadow PARENT`: the full gate, in the land's shape, and its
# verdict judged against the land's (push_release_shadow; GOH.LANDEDREUSEON).
push_release_base() {
    local on="" out=""
    [ -f "$1/.gatesrc" ] && on="$(bash -c 'set -a; . "$1"; printf %s "${GOH_PUSH_RELEASE_REUSE-}"' _ \
        "$1/.gatesrc" 2>/dev/null </dev/null)"
    [ "$on" = on ] || return 0
    out="$("$GOH_PY" "$GOH/lib/release_gate.py" push-decide --root "$2" </dev/null)" \
        || out="full the release rule could not decide (above)"
    case "$out" in
        "release "?*) printf '%s\n' "${out#release }" ;;
        "shadow "?*) printf '%s\n' "$out"
            info "pre-push: the \`landed\` sample: the full gate in the land's shape, judged against ${out:7:8}" >&2 ;;
        *) info "pre-push: the full gate: ${out#full }" >&2 ;;
    esac
}

# push_release_run BASE -- in the export: the land's own reduced set (release_gate.py run), whose
# full-gate fallback is a refusal here: push-decide already chose, so a re-decision that disagrees
# is a tree that moved, not a reason to run a gate without its marks.
push_release_run() {
    "$GOH_PY" "$GOH/lib/release_gate.py" run --root . --base "$1" \
        --full 'echo "pre-push: the release rule decided twice and disagreed -- nothing pushed" >&2; exit 1'
}

# push_release_shadow PARENT STATUS RERUN... -- the `landed` sample's verdict (lib/release_gate.py
# shadow-judge): the full gate's STATUS against PARENT's land green; a red runs RERUN (the same
# full gate) once more first, and only a red both runs repeat turns the reuse off. Returns STATUS:
# the push is the first run's verdict.
push_release_shadow() {
    local parent="$1" status="$2" fresh=green again="" rc=0
    shift 2
    if [ "$status" -ne 0 ]; then
        fresh=red
        "$@" || rc=$?
        again="$([ "$rc" -eq 0 ] && echo green || echo red)"
    fi
    "$GOH_PY" "$GOH/lib/release_gate.py" shadow-judge --root "$worktree" --parent "$parent" \
        --fresh "$fresh" ${again:+--again "$again"} ${log:+--log "$log"} </dev/null \
        || warn "pre-push: the \`landed\` sample disagreed with ${parent:0:8}'s land (reuse_audit.py report)"
    return "$status"
}
