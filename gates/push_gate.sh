#!/usr/bin/env bash
# push_gate.sh — run the full gate on the COMMIT being pushed, not on the working tree.
#
# THE HOLE. `pre-push` used to `exec tools/gate.sh --full` in the checkout, so the gate built and
# tested whatever was on disk at that moment. Measured 2026-09-21 on a private Swift app: a push's
# cold gate was in its test phase (`swift test` recompiles changed sources) while the next round's
# edits were landing in Sources/ — so the gate certified a tree that was neither the pushed commit
# nor any commit, and would have pushed a docs change on the strength of tests run over unrelated,
# unfinished code. The other direction is worse and quieter: uncommitted fixes on disk make a
# broken commit pass. A pre-push gate that reads the working tree is a gate on the wrong object.
#
# THE FIX. For each ref being pushed, check the pushed sha out into a throwaway `git worktree`
# and run the repo's own `tools/gate.sh --full` THERE. The worktree has the tracked files and
# nothing else, which is exactly what the remote will have -- with one documented exception:
# `GOH_EXPORT_KEEP` in `.gatesrc` names ignored files the build genuinely reads (a compile-flag
# marker, a local toolchain pin); they are copied in so the gate tests the tree AS CONFIGURED.
# Cold-build cost is unchanged (the swift gate wiped .build anyway); the checkout is ~1 s.
#
# The worktree lives under ~/.cache/goh/push (GOH_PUSH_WORKTREES overrides), never inside the
# repo (a nested worktree is a tracked-file scan's worst day) and never under the temp directory
# (see the SwiftLint note below).
#
# A RUN OWNS ONE DIRECTORY: <export root>/<repo>-<hash>/<repo>, with `.owner` (pid + start time) beside
# the tree. Measured 2026-10-05: the export root held a three-day-old worktree and 230 `*.out` files.
# The worktree sat directly in the shared root, so whatever a gate wrote BESIDE its tree (a private consumer's
# tests write "$HERE.out") outlived every run; and the promise that the next run's `git worktree
# prune` cleans up after a SIGKILL was false -- prune forgets a worktree whose DIRECTORY is gone, and
# a killed run's directory is not gone. Now cleanup removes the whole run directory on every exit
# path and NAMES anything found outside the tree, and each run first reaps any run directory whose
# owner is dead (tests/test_push_gate_runs.py).
#
# Protocol: git feeds `<local ref> <local sha> <remote ref> <remote sha>` lines on stdin, and passes
# the remote's name as $1. A delete (local sha all zeros) has nothing to test. Several refs are
# gated one after another; a failure stops the push.
#
# WHAT IS NOT GATED, and why that is not a pass over nothing. The gate certifies code the remote is
# about to GAIN. Two kinds of ref gain it none, and each is skipped with a line saying so:
#   - a ref whose commit the remote already has (reachable from one of its tracking refs). A private Swift app,
#     2026-09-22: `git push --follow-tags` carried six release tags that had never been pushed, all
#     on commits the remote had held for days. The gate rebuilt the oldest with today's toolchain,
#     failed on a switch a later SDK made non-exhaustive, and refused a push whose only new code was
#     elsewhere. Six tags would also have cost six cold gates.
#   - a ref whose commit was already gated earlier in this same push. A release pushes `main` and
#     `vX.Y.Z` on one commit; one gate certifies both.
#
# A RED RUN KEEPS ITS EVIDENCE. A private Swift app, 2026-09-22: a push was refused, the caller had
# kept only the last lines of the output, and the re-run of the same commit passed. The failure was
# a flake, and nobody could say which test, because the only copy of the gate's output was a
# scrollback that no longer existed. Every run is now teed to a log under ~/.cache/goh/push-logs
# (GOH_PUSH_LOGS overrides). A green run deletes its log; a red one keeps it, prints its path, and
# the newest `keep_failed_logs` are kept. The cost: the gate writes to a pipe, so no colours.
#
# The pre-push gate every installed hook delegates to: refuses a release tag its own commit does
# not declare (`goh tag-version`, over the refs git hands the hook) BEFORE any worktree is made,
# then takes a machine-wide gate slot (`lib/gate_slot.sh`, `GOH_GATE_SLOTS`), runs `tools/gate.sh
# --full` in a clean export of each pushed commit (and refuses a run that wrote the repository's
# own `.git/config`, naming each key with userinfo redacted, while a ref that moved during the run
# is named, not refused: `lib/repo_writes.sh`, `test_push_gate_repo_writes.py`), skips a commit the
# remote already has, and refuses a branch that moved while it was gated. The steps the repo's
# `.gatesrc` declares in `GOH_CI_STEPS` run too: a gate that passed without `local_ci.sh` marking
# this tree (`GOH_CI_STEPS_MARK`) gets them run after it, the gap named. Invoked by
# `hooks/pre-push`. Pinned by `test_push_gate_ci_steps.py`.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail
# The hook's repository variables go first, before anything is spawned (gates/_git_env.sh, the
# helper pre-commit uses too). Pushed from a LINKED worktree, git hands this hook
# GIT_DIR=<main>/.git/worktrees/<name>: inherited, it points every git call in the export at the
# PUSHING checkout, and a test that runs `git init <tmp>` re-initialises the real repository and
# writes core.bare=true into its shared config (a private consumer, 2026-09-27); `git -C "$GOH" status` below
# listed the pusher's files against the gates checkout as uncommitted gate source.
goh_hook_unbind || exit $?
push_class="${GOH_GATE_CLASS:-}"   # read before the slot's admission consumes it (GOH.BUILDDIRSHARE)

GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"
. "$GOH/tui/lib.sh"
# shellcheck source=lib/bench_lock.sh
. "$GOH/lib/bench_lock.sh"
# shellcheck source=lib/sleep_mark.sh
. "$GOH/lib/sleep_mark.sh"
# shellcheck source=lib/repo_writes.sh
. "$GOH/lib/repo_writes.sh"
# shellcheck source=lib/push_runs.sh
. "$GOH/lib/push_runs.sh"; . "$GOH/lib/suite_cache_guard.sh"; . "$GOH/lib/timing_ledger.sh"; . "$GOH/lib/sccache_dir.sh"   # the last pins SCCACHE_DIR before any repo code runs
# shellcheck source=lib/gate_slot.sh
. "$GOH/lib/gate_slot.sh"
# shellcheck source=gates/_push_refusals.sh
. "$GOH/gates/_push_refusals.sh"   # tag_check_run, push_refusals
bench_lock_join "pre-push" # the whole push is one registration; a measurement waits for it

root="$(git rev-parse --show-toplevel)"
"$GOH_PY" -S "$GOH/lib/repo_hold.py" check "$root" --action "pre-push: this push" || exit 1  # held: no push
gate="$root/tools/gate.sh"
[ -f "$gate" ] || die "pre-push: $root/tools/gate.sh missing — run '$GOH/install.sh $root'"
tag_check="$GOH/gates/goh.sh"
# Named, not a shell error: without this the refusal below reads "No such file or directory:
# …/goh.sh", which names a path the reader has no way to act on. It is fail-closed either way — an
# absent gate is not a pass — and a broken shared checkout is the one case where refusing to push
# cannot be the answer.
[ -f "$tag_check" ] || die "pre-push: $tag_check missing from the gates checkout at $GOH — nothing pushed"

# CLOSED BY C4 (gates/_from_head.sh): this script, and every gate it runs, now runs from an export
# of the gates checkout's HEAD, so the paragraph below describes GOH_LIVE=1 only -- a developer
# asking for the working tree on purpose -- and the check stays for exactly that case.
#
# THE GATE THAT WILL JUDGE THIS PUSH MAY NOT BE COMMITTED. Every step the export
# runs is a file under $GOH — read from the shared checkout's WORKING TREE at the
# moment it runs, not from the export worktree this script builds, because that
# is where `GOH_DIR` points. Appending one comment line to one of them therefore
# changes the answer for every repo whose hooks delegate here, with no reinstall
# and no output. docs/SUPERSOTA.md §3 calls this the highest-severity gap there,
# and nothing named it.
#
# THIS WARNS AND GATES ANYWAY, and the reason is measured rather than assumed.
# The obvious severity — refuse — was written, and it turns out to be the one
# thing that must not happen: this script is spawned by every test in the estate
# that exercises a push, each of them pointing GOH_DIR at this checkout, so a
# refusal here is 22 tests red on every session that has uncommitted work in
# gates_of_heck, including a session's own box halfway through. That is a gate
# whose verdict depends on the working tree — the same defect as the leak this
# file's `.gatesrc` handling used to have, one level up. So the refusal lives
# where it can be seen without being observed by a test: `scripts/build-goh.sh`
# builds bin/goh from an export of HEAD only (C3), so the binary half is closed by
# construction -- a dirty tree cannot reach it at all.
#
# So the two halves are: the binary is HEAD's (nothing observes it by accident),
# certify names itself (twice — here, where a human reads the scrollback, and in
# structural.sh, which every repo's pre-commit runs). A certificate produced while
# this warning is on screen says so, which is more than the estate had.
#
# WHAT COUNTS AS DIRTY, and the paths, are the ones structural.sh documents at
# its own top: anything `git status --porcelain` reports under `gates checks lib
# tui` — modified, staged, deleted, renamed, or untracked-and-not-ignored. The
# list is spelled out rather than sourced from a helper because a helper under
# gates/ is itself gate source this check cannot vouch for;
# tests/test_goh_structural.py pins the lists to each other.
gate_dirty="$(GIT_OPTIONAL_LOCKS=0 git -C "$GOH" status --porcelain --untracked-files=normal \
    -- gates checks lib tui 2>/dev/null || true)"
if [ -n "$gate_dirty" ]; then
    warn "the gates judging this push are NOT committed — the export runs them from $GOH:"
    printf '%s\n' "$gate_dirty" | sed 's/^/    /' >&2
    warn "  …not from $root. What this push is certified by exists in no commit."
    warn "  GOH_LIVE=1 asked for the working tree; without it this push would run HEAD's gates (C4)."
    warn "  Gating anyway: the result below is real, and it is not reproducible from any commit."
fi
unset gate_dirty

# `.gatesrc` IS READ HERE. It is not put into this shell's environment, and it
# is not put into the export gate's either.
#
# It used to be, via `set -a` around the source, and that exported EVERY key the
# repo declares to the gate the push actually runs. Measured 2026-10-02: a push
# of this repo exported `GOH_SKILLS_ROOT=$HOME/.claude/skills`,
# `GOH_SKILLS_CORPUS=1` and `GOH_PYTHON_FORMATTED=1`; the export gate ran
# `tools/gate.sh --full`; the pytest suite it runs inherited all three while
# every fixture repo in it declared NONE of them. Seven tests went red on a tree
# that was green in the checkout, and the push gate refused pushes that passed
# locally -- a gate whose result depends on what a parent happened to export.
#
# Two keys are wanted from the file, and each is taken by name instead:
#   - GOH_EXPORT_KEEP, for the ignored files the export must carry. Read in a
#     CHILD, so a consumer's `export GOH_…` line (three write one) cannot reach
#     this shell's environment either -- the leak does not need `set -a`.
#   - the tag check's keys, read from the ENVIRONMENT by check_tag_version.py.
#     A key documented as "set this in .gatesrc" that the checker can never see
#     is worse than an undocumented one: a private Swift app set it exactly as documented, the
#     checker silently fell back to its defaults, and the pre-push refused a
#     correct release with "NO version source declares a version at this commit"
#     -- the config was read and ignored. Its own subshell gets the export.
GOH_EXPORT_KEEP=""
if [ -f "$root/.gatesrc" ]; then
    GOH_EXPORT_KEEP="$(bash -c 'set -a; . "$1"; printf %s "${GOH_EXPORT_KEEP-}"' _ \
        "$root/.gatesrc")" || die "pre-push: cannot read $root/.gatesrc"
fi

zero="0000000000000000000000000000000000000000"
remote_name="${1:-}"
remote_url="${2:-}"
gated_commits=" "
log_root="${GOH_PUSH_LOGS:-$HOME/.cache/goh/push-logs}"
keep_failed_logs=20
worktree="" run_dir="" refs_file="" kept_tree=""
export_root="${GOH_PUSH_WORKTREES:-$HOME/.cache/goh/push}"; goh_suite_cache_ok "$log_root" "the push log" && goh_suite_cache_ok "$export_root" "the push worktree" || exit 2

cleanup_run() {
    if [ -n "$kept_tree" ] && [ -d "$worktree" ] && push_keep_tree "$root" "$worktree" "$kept_tree"; then
        :   # kept for the next run, its mtimes with it (push_export_tree, lib/push_runs.sh; GOH.PUSHMTIME)
    elif [ -n "$worktree" ] && [ -d "$worktree" ]; then
        git -C "$root" worktree remove --force "$worktree" >/dev/null 2>&1 || goh_rm_tree "$worktree"
    fi
    if [ -n "$run_dir" ] && [ -d "$run_dir" ]; then
        local litter
        litter="$(run_litter "$run_dir" "$worktree")"
        # Named, not swept silently: the defect is the consumer's to fix.
        [ -n "$litter" ] && warn "pre-push: the gate wrote outside its tree (removed): $litter"
        goh_rm_tree "$run_dir"
    fi
    worktree=""
    run_dir=""
}
cleanup() {
    cleanup_run
    [ -n "$refs_file" ] && rm -f "$refs_file"
    if [ -n "$BENCH_LOCK_ENTRY" ]; then rm -f "$BENCH_LOCK_ENTRY"; fi
    gate_slot_release
}
trap cleanup EXIT
trap 'cleanup; exit 129' HUP
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

[ -d "$export_root" ] && reap_dead_runs "$export_root"
git -C "$root" worktree prune >/dev/null 2>&1 || true

# THE REFS ARE READ ONCE, HERE. git hands a pre-push hook its refs on stdin, and
# stdin is a stream: the tag check below needs them and so does the loop after
# it, and a stream read twice is a stream read once. Captured to a file (a
# variable would be fine too — ref lines carry no NUL — but a file also survives
# `set -u` on a push with no refs at all).
refs_file="$(mktemp "$(goh_tmp)/goh-push-refs.XXXXXX")"
cat >"$refs_file"

push_refusals   # the tag, disk, commit-message and landed-only refusals (gates/_push_refusals.sh)

# Every commit this push names, so a ref on an ANCESTOR of another is gated with it, not alone:
# tag_version wants an unpushed release tag in the same push as its branch, and gating the tag's
# old commit with today's gates refused a push no shape could pass (ZSUS 2026-11-19).
push_commits=""
while read -r _ref_l sha_l _ref_r _sha_r; do
    [ -n "${sha_l:-}" ] && [ "$sha_l" != "$zero" ] || continue
    push_commits="$push_commits $(git -C "$root" rev-parse --verify --quiet "${sha_l}^{commit}" || echo "$sha_l")"
done <"$refs_file"

gated=0
gated_how=()   # one COMMIT:HOW word per gated commit, an array: never word-split (GOH.NITS3)
# <"$refs_file", NOT stdin: `cat` above drained it, and a while-read on a spent
# stream is a loop that never runs — a gate that silently gates nothing.
# The full gate, in the export (cwd): tools/gate.sh --full, then the structural layer and the steps
# .gatesrc declares where it ran neither. The `landed` sample runs it in the land's suite shape
# (tests/suite_home.py SHAPE), once more on a red (push_release_shadow, lib/push_runs.sh).
push_full_gate() {
    [ -z "${shadow_base:-}" ] || export GATES_OF_HECK_SUITE_SHAPE=ci
    # shellcheck source=/dev/null # the .gatesrc below is the gated repo's, unknown at lint (GOH.SHX2)
    GOH_CROSS_REPO_ROOT="$root" GOH_CHANGE_BASE="$change_base" GOH_STRUCTURAL_MARK="$structural_mark" \
        GOH_CI_STEPS_MARK="$steps_mark" bash "$worktree/tools/gate.sh" --full \
    && { grep -qFx -- "--full	$(git rev-parse --show-toplevel)" "$structural_mark" || {
            warn "pre-push: tools/gate.sh --full passed without running the house structural layer -- running it now"
            bash "$GOH/gates/structural.sh" --full
        }; } \
    && { [ -z "$( (unset GOH_CI_STEPS; [ ! -f .gatesrc ] || . ./.gatesrc >/dev/null 2>&1 </dev/null
                       printf '%s' "${GOH_CI_STEPS:-}") )" ] \
        || grep -qFx -- "$(git rev-parse --show-toplevel)" "$steps_mark" || {
            warn "pre-push: tools/gate.sh --full passed without running the steps .gatesrc declares (GOH_CI_STEPS) -- running them now"
            GOH_DIR="$GOH" bash "$GOH/gates/local_ci.sh" "$worktree"
        }; }
}
push_full_gate_again() {
    (cd "$worktree" && push_full_gate) 2>&1 | tee -a "$log"
    return "${PIPESTATUS[0]}"
}

while read -r local_ref local_sha _remote_ref remote_sha; do
    [ -n "${local_sha:-}" ] || continue
    [ "$local_sha" = "$zero" ] && continue          # a delete: nothing to test
    # A tag's sha is the tag OBJECT; the code under test is the commit it points at.
    commit="$(git -C "$root" rev-parse --verify --quiet "${local_sha}^{commit}" || echo "$local_sha")"
    short="$(git -C "$root" rev-parse --short "$commit")"
    case "$gated_commits" in
        *" $commit "*) info "pre-push: $local_ref @ $short — gated earlier in this push"; continue ;;
    esac
    # NB: both skips below are about CODE, and neither may excuse a TAG's version
    # claim — which is why check_tag_version runs above, over the refs as given.
    descendant=""
    for other in $push_commits; do
        [ "$other" != "$commit" ] && git -C "$root" merge-base --is-ancestor "$commit" "$other" 2>/dev/null \
            && { descendant="$other"; break; }
    done
    if [ -n "$descendant" ]; then
        info "pre-push: $local_ref @ $short — an ancestor of $(git -C "$root" rev-parse --short "$descendant") in this push; its code is gated with it"
        continue
    fi
    if [ -n "$remote_name" ] && [ -n "$(git -C "$root" for-each-ref --contains "$commit" \
            --format='%(refname)' "refs/remotes/$remote_name/")" ]; then
        info "pre-push: $local_ref @ $short — $remote_name already has this commit; nothing new to gate"
        continue
    fi
    # UNDER $HOME, physical path, never under the temp directory. SwiftLint 0.65.1's baseline
    # (repo-relative paths, verified path-independent by a private Swift app's
    # relativize_lint_baseline.py) matches NOTHING when the tree sits under /private/tmp or
    # /private/var/folders -- every recorded violation fires as new -- and matches everything under
    # /Users/... (measured 2026-09-21 with worktrees of one commit at ~/wt, /Users/Shared/wt,
    # <repo>/.build/wt: 0 violations; /private/tmp/wt: 12 -- even against a baseline swiftlint had
    # just written THERE). The cause is inside SwiftLint's path relativisation; the fix that holds
    # without theory is to gate where the tools were calibrated: a directory beside the user's
    # checkouts. `pwd -P` besides, so a symlinked component can never be the difference.
    # A MACHINE-WIDE SLOT before the heavy work (lib/gate_slot.sh). Several sessions pushing at once
    # had no limit: a private consumer's gate was killed mid-test and games' Release rebuild ran out its 900 s step
    # limit at a load average of 49.6 (2026-10-18) -- failures of the box, not of the change. Taken
    # once per push, before the export, and held to exit; every step ceiling starts inside the gate,
    # so the wait is in the push row (`slot_wait_s`, `slot_class`) and charged to no step. The class is
    # the CALLER's (GOH_GATE_CLASS: `goh ship` says `ship`), else `work`, the lowest: a push nobody
    # classed -- the pin sweep's, another repo's -- never goes ahead of a land (GOH.SLOTPRIORITY).
    [ -n "${slot_asked:-}" ] || { goh_slot_asked; gate_slot_acquire "$(basename "$root")"; goh_slot_taken; slot_asked=1; }
    mkdir -p "$export_root"
    # A STABLE path per repo, claimed with an atomic mkdir. ~/.cargo/config.toml keys the build
    # directory by `{workspace-path-hash}`, so a random export path meant every push built every
    # dependency cold into a new build-dir and left it behind (measured 2026-10-05: 24 dirs, 30 GB,
    # three days). A concurrent push of the same repo finds the path held by a live run and takes a
    # private one, with its cargo build-dir INSIDE the run directory so it is removed with it.
    # That build-dir is a CONFIG FILE in the run directory, never CARGO_BUILD_BUILD_DIR: cargo ranks
    # an environment variable above every config file, so the export silently replaced a build-dir
    # the repo names in its own .cargo/config.toml. $run_dir/.cargo/config.toml is an ancestor of the
    # worktree, so cargo ranks it below the repo's own config and above ~/.cargo/config.toml - it
    # redirects exactly the user-global, path-keyed build-dir it exists to redirect.
    export_build_dir=""
    run_dir="$export_root/$(basename "$root")-$(printf '%s' "$root" | shasum -a 256 | cut -c1-12)"
    if ! mkdir "$run_dir" 2>/dev/null; then
        run_dir="$(mktemp -d "$export_root/XXXXXX")"
        export_build_dir="$run_dir/.cargo-build"
        info "pre-push: $(basename "$root")'s export path is held by a live push; using a private one (cold build)"
    fi
    run_dir="$(cd "$run_dir" && /bin/pwd -P)"
    # The stable export is recreated here for every push, so the build dir keyed on its path is warm
    # on purpose. Name the checkout it serves beside it: a reclaimer that evicts build dirs whose
    # sources are gone keeps this one while the checkout exists (scripts' reclaim_build_root.py;
    # tests/test_push_gate_runs.py). The private fallback's build dir dies with its run: no marker.
    [ -n "$export_build_dir" ] || printf '%s\n' "$(cd "$root" && /bin/pwd -P)" >"$run_dir.source"
    shared_build_dir=""
    if [ -n "$export_build_dir" ]; then
        export_build_dir="$run_dir/.cargo-build"
        private_cargo_config "$run_dir" "$export_build_dir"
    elif [ "$push_class" = ship ]; then   # the checkout's own build dir, clean at $commit (gates/_push_build_dirs.py)
        shared_build_dir="$("$GOH_PY" -S "$GOH/gates/_push_build_dirs.py" --shared "$root" "$commit" 2>/dev/null || true)"
        [ -z "$shared_build_dir" ] || private_cargo_config "$run_dir" "$shared_build_dir"
    fi
    owner_stamp "$$" >"$run_dir/.owner"
    # Named after the repo, so a tool that reads its project's name from the directory sees the
    # real one rather than a mktemp suffix.
    worktree="$run_dir/$(basename "$root")"
    section "pre-push: gating $local_ref @ $short in a clean worktree"
    # GOH.TOOLTIMEOUT: a pre-push gate cannot background itself (git waits on its exit to push), so
    # it says, before it runs, how long it runs here and how a caller runs it unwatched.
    p95=$("$GOH_PY" -S "$GOH/lib/timing_ledger.py" p95 --kind push --repo "$root" 2>/dev/null || true)
    if [ -n "$p95" ]; then
        p95="p95 ${p95%% *} s over the last ${p95##* } pushes of $(basename "$root")"
    else
        p95="minutes; no push of $(basename "$root") measured yet"
    fi
    info "pre-push: this gate runs long ($p95): from an agent, run \`git push\` with run_in_background, or hand it to \`goh land --enqueue REPO SHA\`"
    # A ship sharing the checkout's build dir builds from a fresh tree: kept mtimes vouch only for the export's own dir.
    kept_tree="" && [ -n "$export_build_dir$shared_build_dir" ] || kept_tree="$run_dir.kept"
    push_export_tree "$root" "$worktree" "$commit" "$kept_tree"   # lib/push_runs.sh
    export_siblings "$worktree" "$run_dir" "$root"   # every sibling a tracked manifest's path dependency names (lib/push_runs.sh)
    # ...and the build dirs cargo keys on the export, asked while it exists (gates/_push_build_dirs.py).
    [ -n "$export_build_dir" ] || "$GOH_PY" -S "$GOH/gates/_push_build_dirs.py" "$worktree" 2>/dev/null |
        sed 's/^/build-dir /' >>"$run_dir.source" || true
    # An IN-TREE build dir is deleted with the export after every push: every dependency compiles cold,
    # silently (GOH.PUSHINTREEBUILD; 187 s against 108 s warm). Named, so it cannot recur.
    [ -n "$export_build_dir" ] || sed -n 's/^build-dir //p' "$run_dir.source" 2>/dev/null |
        "$GOH_PY" -S "$GOH/gates/_push_build_dirs.py" --in-tree "$worktree" 2>/dev/null |
        while IFS= read -r in_tree_dir; do
            warn "push: $(basename "$root") builds inside the export at $in_tree_dir, deleted after every push: every dependency compiles cold (a tracked .cargo/config.toml build-dir; drop it for the path-keyed default)"
        done || true
    read -ra kept_paths <<<"$GOH_EXPORT_KEEP"   # space-separated, split once, never globbed
    export_kept "$root" "$worktree" "${kept_paths[@]}"   # the ignored files .gatesrc names (lib/push_runs.sh)
    mkdir -p "$log_root"
    log="$log_root/$(basename "$root")-$short-$(date +%Y%m%dT%H%M%S).log"
    set +e
    # No repository variable reaches the export: they were dropped at entry (goh_hook_unbind,
    # above). The export is its own worktree; git finds it from its `.git` file.
    # GOH_CROSS_REPO_ROOT, and why the export is the wrong venue without it. The worktree holds the
    # pushed commit's TRACKED files. A metarepo whose evidence IS its sibling repositories has no
    # tracked children -- they are separate repos, not submodules -- so in here every cross-repo
    # citation resolves to nothing and the gate reports a dozen scripts as "no longer exists in the
    # estate" that are sitting in the checkout, right now, resolvable. Measured 2026-10-03 on
    # games: 9 findings on the commit that exits 0 in the working tree, same checker, same sha.
    #
    # Deliberately NOT a symlink of the children into the worktree: that would put sibling files
    # inside this repo's scanned tree, so a parent's whole-repo scanners would police the children's
    # sources and a parent push would fail on a finding that is the child's to fix. This names the
    # real checkout instead, and a checker reads it only for citations that name a child. The
    # isolation the worktree buys -- never certify the working tree -- is untouched: this repo's
    # OWN files still come from the worktree.
    change_base="$(push_change_base "$root" "$commit" "${remote_sha:-$zero}" "$remote_name")"   # lib/push_runs.sh
    repo_before="$(goh_repo_snapshot "$root")"   # what the gate may not write (lib/repo_writes.sh)
    slept_mark="$(goh_sleep_mark)"
    # The gate's checks for its ledger row (CLS.CIPROFILE, `goh budget gates`): a push nobody profiles
    # times its steps into a file of its own, removed after the row; a land or a profile hands one.
    own_timings=""
    if [ -z "${GOH_TIMINGS:-}" ]; then
        own_timings="$(mktemp "$(goh_tmp)/goh-push-timings.XXXXXX")" && export GOH_TIMINGS="$own_timings" || own_timings=""
    fi
    goh_span_start   # the full gate's START: after the slot, the export and the checks (lib/budget.py KINDS)
    # The structural layer and the steps .gatesrc DECLARES run whatever the repo's gate calls (6.7,
    # test_push_gate_ci_steps.py), each marking its tree; a release-only commit: lib/push_runs.sh.
    release_base="$(push_release_base "$root" "$worktree")"
    config_base="$([ -n "$release_base" ] || push_config_base "$worktree" "${remote_sha:-$zero}")"   # floors only
    structural_mark="$(mktemp "$(goh_tmp)/goh-structural-mark.XXXXXX")"
    steps_mark="$(mktemp "$(goh_tmp)/goh-ci-steps-mark.XXXXXX")"
    shadow_base=""   # the audit's `landed` sample: the full gate in the land's shape (lib/push_runs.sh)
    case "$release_base" in "shadow "*) shadow_base="${release_base#shadow }" release_base="" ;; esac
    (cd "$worktree" && if [ -n "$release_base" ]; then
            GOH_CROSS_REPO_ROOT="$root" GOH_CHANGE_BASE="$change_base" push_release_run "$release_base"
        elif [ -n "$config_base" ]; then GOH_CROSS_REPO_ROOT="$root" GOH_CHANGE_BASE="$change_base" push_config_run "$config_base"
        else push_full_gate; fi) 2>&1 | tee "$log"
    status="${PIPESTATUS[0]}"
    [ -z "$shared_build_dir" ] || "$GOH_PY" -S "$GOH/gates/_push_build_dirs.py" --touch-changed "$root" || true
    [ -z "$shadow_base" ] || push_release_shadow "$shadow_base" "$status" push_full_gate_again || true
    # The full gate's END: one ledger row (GOH.GATEBUDGET); none for a release-only or floor push, none under a land
    [ -n "$release_base$config_base${GOH_TIMING_VIA:-}" ] || goh_push_row "$root" "$status" "$commit"
    [ -z "$own_timings" ] || { rm -f "$own_timings"; unset GOH_TIMINGS; }
    rm -f "$structural_mark" "$steps_mark"
    set -e
    goh_repo_writes "$root" "$repo_before" || { err "pre-push: nothing pushed"; exit 1; }
    if [ "$status" -ne 0 ]; then
        err "pre-push: the gate failed on $short — nothing pushed"
        err "pre-push: the full gate output is kept at $log"
        goh_slept_since "$slept_mark" || true
        push_prune_logs "$log_root" "$keep_failed_logs"   # bounded: the newest failures are evidence
        exit 1
    fi
    rm -f "$log"
    cleanup_run
    gated=$((gated + 1))
    gated_commits="$gated_commits$commit "
    gated_refs="${gated_refs:-}$local_ref=$commit "
    gated_how+=("$commit:${release_base:+release}${config_base:+config}")   # empty: full
done <"$refs_file"

# THE BRANCH MUST STILL BE WHAT WAS GATED (D-0211 of a Swift app, tests/test_push_gate_race.py).
# Over HTTPS git sends what the local ref names when the helper SENDS, after this hook returns:
# a 25-minute gate of d0cf1968 delivered f79cf19f, two commits no gate ran on. Re-read every
# gated ref now; one that moved is refused. A pinned `<sha>:refs/heads/x` push resolves to itself.
for pair in ${gated_refs:-}; do
    ref="${pair%%=*}" want="${pair#*=}"
    now="$(git -C "$root" rev-parse --verify --quiet "${ref}^{commit}" || echo "(gone)")"
    if [ "$now" != "$want" ]; then
        err "pre-push: $ref moved while it was being gated: gated ${want:0:12}, now ${now:0:12}"
        err "  git would send ${now:0:12}, which no gate ran on -- nothing pushed. Push again, or pin"
        err "  the commit:  git push <remote> ${want:0:12}:<branch>"
        exit 1
    fi
done
push_record_verdicts "$root" "${gated_how[@]}"   # each green in the one verdict store (lib/push_runs.sh, lib/reuse.py)
[ "$gated" -gt 0 ] || info "pre-push: nothing to gate (deletes, or commits the remote already has)"
command exit
} # parse-guard
