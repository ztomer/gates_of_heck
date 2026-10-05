#!/usr/bin/env bash
# push_gate.sh — run the full gate on the COMMIT being pushed, not on the working tree.
#
# THE HOLE. `pre-push` used to `exec tools/gate.sh --full` in the checkout, so the gate built and
# tested whatever was on disk at that moment. Measured 2026-09-21 on ZoneWM: a push's cold gate
# was in its test phase (`swift test` recompiles changed sources) while the next round's edits
# were landing in Sources/ — so the gate certified a tree that was neither the pushed commit nor
# any commit, and would have pushed a docs change on the strength of tests run over unrelated,
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
# The worktree sat directly in the shared root, so whatever a gate wrote BESIDE its tree (Finance's
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
#   - a ref whose commit the remote already has (reachable from one of its tracking refs). ZoneWM,
#     2026-09-22: `git push --follow-tags` carried six release tags that had never been pushed, all
#     on commits the remote had held for days. The gate rebuilt the oldest with today's toolchain,
#     failed on a switch a later SDK made non-exhaustive, and refused a push whose only new code was
#     elsewhere. Six tags would also have cost six cold gates.
#   - a ref whose commit was already gated earlier in this same push. A release pushes `main` and
#     `vX.Y.Z` on one commit; one gate certifies both.
#
# A RED RUN KEEPS ITS EVIDENCE. ZoneWM, 2026-09-22: a push was refused, the caller had kept only the
# last lines of the output, and the re-run of the same commit passed. The failure was a flake, and
# nobody could say which test, because the only copy of the gate's output was a scrollback that no
# longer existed. Every run is now teed to a log under ~/.cache/goh/push-logs (GOH_PUSH_LOGS
# overrides). A green run deletes its log; a red one keeps it, prints its path, and the newest
# `keep_failed_logs` are kept. The cost: the gate writes to a pipe, so its colours are off.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail

GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"
. "$GOH/tui/lib.sh"

root="$(git rev-parse --show-toplevel)"
gate="$root/tools/gate.sh"
[ -f "$gate" ] || die "pre-push: $root/tools/gate.sh missing — run '$GOH/install.sh $root'"
tag_check="$GOH/checks/check_tag_version.py"
# Named, not a Python traceback: without this the refusal below reads
# "can't open file …/check_tag_version.py", which names a path the reader has no
# way to act on. It is fail-closed either way — an absent gate is not a pass —
# and a broken shared checkout is the one case where refusing to push cannot be
# the answer.
[ -f "$tag_check" ] || die "pre-push: $tag_check missing from the gates checkout at $GOH — nothing pushed"

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
# tests/test_goh_structural_parity.py pins the lists to each other.
gate_dirty="$(git -C "$GOH" status --porcelain --untracked-files=normal \
    -- gates checks lib tui 2>/dev/null || true)"
if [ -n "$gate_dirty" ]; then
    warn "the gates judging this push are NOT committed — the export runs them from $GOH:"
    printf '%s\n' "$gate_dirty" | sed 's/^/    /' >&2
    warn "  …not from $root. What this push is certified by exists in no commit."
    warn "  Commit or stash them before pushing; scripts/build-goh.sh refuses to publish bin/goh here."
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
#     is worse than an undocumented one: ZoneWM set it exactly as documented, the
#     checker silently fell back to its defaults, and the pre-push refused a
#     correct release with "NO version source declares a version at this commit"
#     -- the config was read and ignored. Its own subshell gets the export.
GOH_EXPORT_KEEP=""
if [ -f "$root/.gatesrc" ]; then
    GOH_EXPORT_KEEP="$(bash -c 'set -a; . "$1"; printf %s "${GOH_EXPORT_KEEP-}"' _ \
        "$root/.gatesrc")" || die "pre-push: cannot read $root/.gatesrc"
fi

# The tag check, run with .gatesrc in the ENVIRONMENT and nothing else changed.
# IN A SUBSHELL, and that is the load-bearing word: `set -a` marks the shell that
# runs it, and `set +a` stops new assignments being exported -- it does NOT take
# the export attribute back off the keys already assigned, so a function called
# directly from this shell left .gatesrc's keys exported for the rest of the
# hook, which is the leak this whole block exists to close. A subshell's export
# table dies with it.
tag_check_run() {
    if [ -f "$root/.gatesrc" ]; then
        set -a
        # shellcheck source=/dev/null
        . "$root/.gatesrc"
        set +a
    fi
    python3 -B "$tag_check" --root "$root" --refs-file "$refs_file"
}

zero="0000000000000000000000000000000000000000"
remote_name="${1:-}"
gated_commits=" "
log_root="${GOH_PUSH_LOGS:-$HOME/.cache/goh/push-logs}"
keep_failed_logs=20
worktree=""
run_dir=""
refs_file=""
export_root="${GOH_PUSH_WORKTREES:-$HOME/.cache/goh/push}"

# `<pid> <start time>`: the identity of a live process. A pid alone is reused; the pair is not.
owner_stamp() {
    printf '%s %s\n' "$1" "$(ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//')"
}

cleanup_run() {
    if [ -n "$worktree" ] && [ -d "$worktree" ]; then
        git -C "$root" worktree remove --force "$worktree" >/dev/null 2>&1 || rm -rf "$worktree"
    fi
    if [ -n "$run_dir" ] && [ -d "$run_dir" ]; then
        local litter
        # `|| true`: an empty list is grep's exit 1, and under pipefail that killed the cleanup.
        litter="$(cd "$run_dir" && ls -A | { grep -vx -e .owner -e .cargo-build -e "$(basename "$worktree")" || true; } | tr '\n' ' ')"
        # Named, not silently swept: a gate that writes outside its own tree writes into the
        # operator's checkout parent when it runs there. The defect is the consumer's to fix.
        [ -n "$litter" ] && warn "pre-push: the gate wrote outside its tree (removed): $litter"
        rm -rf "$run_dir"
    fi
    worktree=""
    run_dir=""
}
cleanup() {
    cleanup_run
    [ -n "$refs_file" ] && rm -f "$refs_file"
}
trap cleanup EXIT
trap 'cleanup; exit 129' HUP
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

# Reap what killed runs left: a run directory whose owner is not alive. Only directories carrying an
# `.owner` are judged; anything else in the root is not this layout's to delete.
reap_dead_runs() {
    local dir pid
    for dir in "$export_root"/*/; do
        [ -f "$dir.owner" ] || continue
        read -r pid _ <"$dir.owner" || continue
        if [ "$(owner_stamp "$pid")" = "$(cat "$dir.owner")" ] && kill -0 "$pid" 2>/dev/null; then
            continue
        fi
        info "pre-push: removing a run left by a killed gate: ${dir%/}"
        rm -rf "$dir"
    done
}
[ -d "$export_root" ] && reap_dead_runs
git -C "$root" worktree prune >/dev/null 2>&1 || true

# THE REFS ARE READ ONCE, HERE. git hands a pre-push hook its refs on stdin, and
# stdin is a stream: the tag check below needs them and so does the loop after
# it, and a stream read twice is a stream read once. Captured to a file (a
# variable would be fine too — ref lines carry no NUL — but a file also survives
# `set -u` on a push with no refs at all).
refs_file="$(mktemp "${TMPDIR:-/tmp}/goh-push-refs.XXXXXX")"
cat >"$refs_file"

# A TAG IS A CLAIM ABOUT A VERSION, and it is the one claim the code gate cannot
# make: `tools/gate.sh --full` proves the commit BUILDS AND PASSES, never that
# the name someone published for it is true. media_server, 2026-10-01: two
# `--amend --no-edit` runs were rejected by pre-commit with `2>/dev/null`
# swallowing the refusal, so `git tag -f v1.79.3` named a commit still declaring
# 1.79.1 and `--follow-tags` shipped it. Anyone checking out v1.79.3 got a build
# that called itself 1.79.1 — the same class as a version string that cannot
# tell two binaries apart, except the name outlives the mistake.
#
# It runs FIRST, over the refs as given, because everything below it can `continue`:
# a commit already on the remote, or already gated earlier in this push, would
# otherwise let a lying tag through unexamined -- and a tag whose commit the remote
# has held for days is EXACTLY the retag case. Refusing the push also skips minutes
# of cold gate work that a refused push would only throw away.
# -B: no bytecode. Running a Python checker from the gates checkout writes
# __pycache__/ INTO it, and the proven-step cache keys on that checkout's
# untracked contents (_proven.sh: _proven_goh_identity). A checkout that does not
# gitignore bytecode therefore changes identity part-way through a push, and
# every step the pre-commit hook proved is re-run in the export — measured by
# tests/test_proven.py, whose gates copy is exactly such a checkout. Reading a
# shared directory must never modify it.
if ! ( tag_check_run ); then
    err "pre-push: a tag being pushed does not match the version its own commit declares — nothing pushed"
    exit 1
fi

gated=0
# <"$refs_file", NOT stdin: `cat` above drained it, and a while-read on a spent
# stream is a loop that never runs — a gate that silently gates nothing.
while read -r local_ref local_sha _remote_ref _remote_sha; do
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
    if [ -n "$remote_name" ] && [ -n "$(git -C "$root" for-each-ref --contains "$commit" \
            --format='%(refname)' "refs/remotes/$remote_name/")" ]; then
        info "pre-push: $local_ref @ $short — $remote_name already has this commit; nothing new to gate"
        continue
    fi
    # UNDER $HOME, physical path, never under the temp directory. SwiftLint 0.65.1's baseline
    # (repo-relative paths, verified path-independent by ZoneWM's relativize_lint_baseline.py)
    # matches NOTHING when the tree sits under /private/tmp or /private/var/folders -- every
    # recorded violation fires as new -- and matches everything under /Users/... (measured
    # 2026-09-21 with worktrees of one commit at ~/wt, /Users/Shared/wt, <repo>/.build/wt: 0
    # violations; /private/tmp/wt: 12 -- even against a baseline swiftlint had just written
    # THERE). The cause is inside SwiftLint's path relativisation; the fix that holds without
    # theory is to gate where the tools were calibrated: a directory beside the user's checkouts.
    # `pwd -P` besides, so a symlinked component can never be the difference.
    mkdir -p "$export_root"
    # A STABLE path per repo, claimed with an atomic mkdir. ~/.cargo/config.toml keys the build
    # directory by `{workspace-path-hash}`, so a random export path meant every push built every
    # dependency cold into a new build-dir and left it behind (measured 2026-10-05: 24 dirs, 30 GB,
    # three days). A concurrent push of the same repo finds the path held by a live run and takes a
    # private one, with its cargo build-dir INSIDE the run directory so it is removed with it.
    export_build_dir=""
    run_dir="$export_root/$(basename "$root")-$(printf '%s' "$root" | shasum -a 256 | cut -c1-12)"
    if ! mkdir "$run_dir" 2>/dev/null; then
        run_dir="$(mktemp -d "$export_root/XXXXXX")"
        export_build_dir="$run_dir/.cargo-build"
        info "pre-push: $(basename "$root")'s export path is held by a live push; using a private one (cold build)"
    fi
    run_dir="$(cd "$run_dir" && pwd -P)"
    [ -n "$export_build_dir" ] && export_build_dir="$run_dir/.cargo-build"
    owner_stamp "$$" >"$run_dir/.owner"
    # Named after the repo, so a tool that reads its project's name from the directory sees the
    # real one rather than a mktemp suffix.
    worktree="$run_dir/$(basename "$root")"
    section "pre-push: gating $local_ref @ $short in a clean worktree"
    git -C "$root" worktree add --detach --quiet "$worktree" "$commit"
    for f in $GOH_EXPORT_KEEP; do
        if [ -e "$root/$f" ]; then
            mkdir -p "$worktree/$(dirname "$f")"
            cp -R "$root/$f" "$worktree/$f"
            info "carried ignored file into the export: $f"
        fi
    done
    mkdir -p "$log_root"
    log="$log_root/$(basename "$root")-$short-$(date +%Y%m%dT%H%M%S).log"
    set +e
    # The hook's repository variables are dropped for the export. Pushed from a LINKED worktree,
    # git hands this hook GIT_DIR=<main>/.git/worktrees/<name>: inherited, it points every git
    # call in the export at the PUSHING checkout, and any test that runs `git init <tmp>` then
    # re-initialises the real repository and writes core.bare=true into its shared config
    # (zinc, 2026-09-27). The export is its own worktree; git finds it from its `.git` file.
    # shellcheck disable=SC2046  # word-splitting git's variable list is the point
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
    # isolation the worktree buys -- never certify the working tree -- is untouched, because what it
    # guards is this repo's OWN files, and those still come from the worktree.
    (unset $(git rev-parse --local-env-vars) && cd "$worktree" \
        && { [ -z "$export_build_dir" ] || export CARGO_BUILD_BUILD_DIR="$export_build_dir"; } \
        && GOH_CROSS_REPO_ROOT="$root" bash "$worktree/tools/gate.sh" --full) 2>&1 | tee "$log"
    status="${PIPESTATUS[0]}"
    set -e
    if [ "$status" -ne 0 ]; then
        err "pre-push: the gate failed on $short — nothing pushed"
        err "pre-push: the full gate output is kept at $log"
        # Bounded: the newest failures are evidence, the rest is clutter.
        find "$log_root" -name '*.log' -type f -print0 | xargs -0 ls -t | tail -n "+$((keep_failed_logs + 1))" \
            | while IFS= read -r old; do rm -f "$old"; done
        exit 1
    fi
    rm -f "$log"
    cleanup_run
    gated=$((gated + 1))
    gated_commits="$gated_commits$commit "
    gated_refs="${gated_refs:-}$local_ref=$commit "
done <"$refs_file"

# THE BRANCH MUST STILL BE WHAT WAS GATED (ZoneWM D-0211, tests/test_push_gate_race.py). Over an
# HTTPS remote git sends what the local ref names when the helper SENDS, after this hook returns:
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

[ "$gated" -gt 0 ] || info "pre-push: nothing to gate (deletes, or commits the remote already has)"
exit
} # parse-guard
