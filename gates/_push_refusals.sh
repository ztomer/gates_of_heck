# _push_refusals.sh -- what gates/push_gate.sh refuses before it builds any export: a release tag
# that does not hold, too little disk for a cold gate, a pushed commit's message breaking a rule, and
# a landed-only branch moved outside tools/land.sh. Split out of push_gate.sh by concern when that
# file stood at the 500-line cap (GOH.CAPHEADROOM); it reads push_gate.sh's globals (root, refs_file,
# remote_name, remote_url, zero, tag_check, export_root) at call time. Sourced, never run.
# shellcheck shell=bash
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_push_refusals.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# The tag check, run with .gatesrc in the ENVIRONMENT and nothing else changed.
# IN A SUBSHELL, and that is the load-bearing word: `set -a` marks the shell that
# runs it, and `set +a` stops new assignments being exported -- it does NOT take
# the export attribute back off the keys already assigned, so a function called
# directly from this shell left .gatesrc's keys exported for the rest of the
# hook, which is the leak this whole block exists to close. A subshell's export
# table dies with it.
# shellcheck disable=SC2154  # root, refs_file, tag_check are push_gate.sh's, set before this runs
tag_check_run() {
    if [ -f "$root/.gatesrc" ]; then
        set -a
        # shellcheck source=/dev/null
        . "$root/.gatesrc"
        set +a
    fi
    # The native port (`goh tag-version`, Phase N1) through the one dispatcher, which
    # refuses when no binary resolves (Phase N3: the binary is the only tier).
    # --remote: a local release tag the pushed branches carry must reach the remote too --
    # `--follow-tags` never sends a lightweight one (servers fleet, 2026-10-10). git always
    # names the remote; a hook run by hand without one skips that half, and only that half.
    # It asks the remote at the URL git hands the hook ($2), never by name alone: gate_profile.sh
    # pushes to a made-up name whose URL is the repo itself.
    bash "$tag_check" tag-version --root "$root" --refs-file "$refs_file" \
        ${remote_name:+--remote "$remote_name"} ${remote_url:+--remote-url "$remote_url"}
}

# Every refusal, in order; each `exit 1`s the push gate itself.
# shellcheck disable=SC2154  # zero, export_root and the rest are push_gate.sh's (header)
push_refusals() {
    # A TAG IS A CLAIM ABOUT A VERSION, and it is the one claim the code gate cannot
    # make: `tools/gate.sh --full` proves the commit BUILDS AND PASSES, never that
    # the name someone published for it is true. A private server workspace, 2026-10-01: two
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
    # contents (lib/gate_identity.py, which skips bytecode). A checkout that does not
    # gitignore bytecode therefore changes identity part-way through a push, and
    # every step the pre-commit hook proved is re-run in the export — measured by
    # tests/test_proven.py, whose gates copy is exactly such a checkout. Reading a
    # shared directory must never modify it.
    # Exit 1 is a finding; anything else is a check that could not judge (no binary, an unreadable
    # refs file, a bad GOH_TAG_VERSION_SOURCES). Both refuse; only the first is a mismatch, and
    # saying "does not match" over a missing binary sends the reader to the wrong tag.
    tag_rc=0
    ( tag_check_run ) || tag_rc=$?
    if [ "$tag_rc" -eq 1 ]; then
        err "pre-push: a release tag this push names or carries does not hold (above) — nothing pushed"
        exit 1
    elif [ "$tag_rc" -ne 0 ]; then
        err "pre-push: the tag check could not run (exit $tag_rc, above) — nothing pushed"
        exit 1
    fi

    # ROOM FOR A COLD GATE (GOH_MIN_FREE_GIB), asked before any export is made. A full disk failed a
    # push as a codesign and a sanitizer error, so the first reading was a race (a private Swift app,
    # 2026-10-06); refused here, it names the free space and the need. Read by name, in a child.
    min_free=""
    if [ -f "$root/.gatesrc" ]; then
        min_free="$(bash -c 'set -a; . "$1"; printf %s "${GOH_MIN_FREE_GIB-}"' _ "$root/.gatesrc")" \
            || die "pre-push: cannot read $root/.gatesrc"
    fi
    if [ -n "$min_free" ]; then
        mkdir -p "$export_root"
        "$GOH_PY" -S "$GOH/lib/preflight_disk.py" "$export_root" "$min_free" \
            || { err "pre-push: not enough room for the cold gate -- nothing pushed"; exit 1; }
    fi

    # THE COMMIT-MESSAGE RULES OVER THE PUSHED RANGE (GOH_COMMIT_CLASS, roadmap Phase 8; GOH_RED_RUN,
    # BACKLOG 4.11). The commit-msg hook asks at commit time, and `--no-verify` skips it; the push asks
    # again, over every commit the remote does not have yet. Read from .gatesrc by name, in a child,
    # like GOH_EXPORT_KEEP.
    commit_class="" red_run=""
    if [ -f "$root/.gatesrc" ]; then
        commit_class="$(bash -c 'set -a; . "$1"; printf %s "${GOH_COMMIT_CLASS-}"' _ \
            "$root/.gatesrc")" || die "pre-push: cannot read $root/.gatesrc"
        red_run="$(bash -c 'set -a; . "$1"; printf %s "${GOH_RED_RUN-}"' _ \
            "$root/.gatesrc")" || die "pre-push: cannot read $root/.gatesrc"
    fi
    if [ -n "$commit_class$red_run" ]; then
        class_rc=0
        while read -r class_ref class_sha _ class_remote; do
            [ -n "${class_sha:-}" ] && [ "$class_sha" != "$zero" ] || continue
            class_commit="$(git -C "$root" rev-parse --verify --quiet "${class_sha}^{commit}")" || continue
            if [ "${class_remote:-$zero}" != "$zero" ] \
                    && git -C "$root" cat-file -e "${class_remote}^{commit}" 2>/dev/null; then
                class_revs=("$class_remote..$class_commit")
            elif [ -n "$remote_name" ] \
                    && [ -n "$(git -C "$root" for-each-ref --count=1 "refs/remotes/$remote_name/")" ]; then
                class_revs=("$class_commit" --not "--remotes=$remote_name")
            else
                info "pre-push: commit-class: nothing bounds $class_ref's new commits (no ref of" \
                    "${remote_name:-the remote}); the commit-msg hook judged each one"
                continue
            fi
            if [ -n "$commit_class" ]; then
                (cd "$root" && bash "$tag_check" commit-class --range "${class_revs[@]}") || class_rc=1
            fi
            if [ -n "$red_run" ]; then
                (cd "$root" && bash "$tag_check" red-run --range "${class_revs[@]}") || class_rc=1
            fi
        done <"$refs_file"
        if [ "$class_rc" -ne 0 ]; then
            err "pre-push: a pushed commit's message breaks a rule above -- nothing pushed"
            exit 1
        fi
    fi

    # A BRANCH THAT MOVES ONLY THROUGH tools/land.sh (GOH_LANDED_ONLY, BACKLOG 1.5). The rule lived in
    # prose, and on 2026-10-10 main moved twice without it: a commit made in the main checkout on a red
    # suite, and a `git merge --ff-only` from /tmp. land.sh records each SHA it gated and merged; a new
    # tip of the named branch that is not in that record is refused, by name, before anything runs.
    landed_only=""
    if [ -f "$root/.gatesrc" ]; then
        landed_only="$(bash -c 'set -a; . "$1"; printf %s "${GOH_LANDED_ONLY-}"' _ \
            "$root/.gatesrc")" || die "pre-push: cannot read $root/.gatesrc"
    fi
    if [ -n "$landed_only" ]; then
        landed="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)/goh-landed"
        while read -r land_ref land_sha _ land_remote; do
            [ "$land_ref" = "refs/heads/$landed_only" ] && [ "$land_sha" != "$zero" ] || continue
            [ "$land_sha" = "${land_remote:-}" ] && continue
            grep -qE "^$land_sha( |\$)" "$landed" 2>/dev/null && continue   # `<sha> [identity]`
            err "pre-push: $landed_only @ ${land_sha:0:8} did not come through tools/land.sh (GOH_LANDED_ONLY):" \
                "nothing in $landed gated it -- land it from a branch worktree (tools/land.sh), then push"
            exit 1
        done <"$refs_file"
    fi
}
