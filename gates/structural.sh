#!/usr/bin/env bash
# structural.sh — the gates every repo gets, whatever it is written in.
#
# Nothing here knows about a toolchain. These are the checks that were being
# re-implemented once per repo (eleven copies of the emoji gate, three
# different names for the file-length cap) and drifting apart as they went.
#
#   structural.sh              # whole tree
#   structural.sh --staged     # staged files only (pre-commit scope)
#
# Config, all optional, read from the TARGET repo's .gatesrc:
#   GOH_MAX_LINES=500          # file-length cap; unset disables the check
#   GOH_LINE_EXCLUDE="re1|re2" # paths exempt from the cap
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/_common.sh"

CHECKS="$(cd "$HERE/../checks" && pwd)"
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
usage: structural.sh [--staged | --full]
  no argument : full-tree checks (same as --full)
  --staged    : staged files only (pre-commit scope, fast)
  --full      : every check (pre-push scope)
EOF
    exit 0
fi
SCOPE="${1:-}"

# Strict scope argument: "" |--full|--staged are the ONLY accepted forms.
# Anything else previously FELL THROUGH to a silent full-tree run — a typo
# (`--stgaed`, `--staged --dry-run`) meant "check everything" without ever
# saying so. Unknown arguments are a usage error naming the accepted forms,
# never a scope guess.
case "$SCOPE" in
    ""|--full|--staged) ;;
    *)
        err "structural.sh: unknown argument '$SCOPE' (accepted: no argument | --full | --staged)"
        exit 2
        ;;
esac
[ $# -le 1 ] || {
    err "structural.sh: unexpected extra arguments: $* (accepted: no argument | --full | --staged)"
    exit 2
}

# ── The gate runs SOURCE, and uncommitted source is a different gate. ───────
#
# Every step below is a Python file under $GOH_DIR/checks, and this script is
# itself a file under $GOH_DIR/gates. Both are read from the shared checkout's
# WORKING TREE, at the moment the gate runs, in whatever repo it is running for.
# Appending one comment line to one of them changes the verdict of every repo
# whose hooks delegate here — with no reinstall, no output and no refusal, and
# that combination is the worst property a gate can have: silent, and total.
#
# This WARNS and runs, at both scopes, and that is deliberate — the refusal is in
# scripts/build-goh.sh, and here is the measurement that put it there. Failing
# here instead looked right and was wrong for two reasons, both found by doing it:
#   - this script is what 30 repos' PRE-COMMIT hooks run. A gate that failed on
#     uncommitted source would stop every repo in the estate committing, over
#     work in a checkout none of them can see or fix.
#   - every test in the estate that spawns this gate with GOH_DIR pointing at a
#     real checkout would go red the moment a session had uncommitted work here.
#     Measured: 22 tests, from one session's own box halfway through. A gate whose
#     verdict depends on the working tree is the same defect as the one two dozen
#     lines below used to have.
# So the shape is: PUBLISH refuses (scripts/build-goh.sh, which install.sh calls,
# so the "no reinstall" half is closed where a reinstall actually happens) and
# CERTIFY names itself — here, in every repo's pre-commit, and again in
# gates/push_gate.sh where a human reads the scrollback. What the gate can no
# longer do is change silently.
#
# WHAT COUNTS AS DIRTY: anything `git status --porcelain` reports under those
# paths — modified, staged, deleted, renamed, or untracked-and-not-ignored.
# Untracked counts because a checker that is written but not yet wired is still
# gate source, and the conservative direction is the one that refuses. Ignored
# build output (`__pycache__/`, `*.pyc`) is excluded by git itself, so running a
# checker never makes the tree dirty and warns about itself.
#
# THE PATHS are what a gate READS OR EXECUTES at run time. `crates/` is
# deliberately in build-goh.sh's list and not in this one: uncommitted Rust
# changes nothing until someone rebuilds bin/goh, which is a publish, and a
# publish is where refusing costs nothing. `install.sh`, `hooks/` and `tools/`
# are read at INSTALL time, not by a gate run. A tree that is not a git checkout
# — a tarball install — has nothing to compare and says nothing, like every other
# gate here.
_goh_gate_source_paths="gates checks lib tui"
_goh_dirty_gate_source() {
    git -C "$GOH_ROOT" status --porcelain --untracked-files=normal \
        -- $_goh_gate_source_paths 2>/dev/null || true
}

# A STEP THAT DOES NOT RUN PRINTS EXACTLY WHAT A STEP THAT PASSES PRINTS.
#
# `goh` carries the pipeline, so a binary built before a step was added is a
# pipeline that does not contain it — and every signal the run emits is
# indistinguishable from a clean run. Measured 2026-10-02: a `bin/goh`
# predating the markdown-links step, so that step did not run, and two
# long-standing broken links sat in the tree the whole time (one a TOC entry
# contradicting the heading it linked to).
#
# `scripts/build-goh.sh` already compares these two numbers — but only INSIDE
# the build, which is why the gap was invisible: nothing between a version bump
# and the next `install.sh` could see it, and 30 repos' hooks read the binary in
# between. This is that comparison, at gate time, where a run of the gate can
# see it.
#
# FAIL-CLOSED, and deliberately so: the alternative is the status quo. The one
# soft case is a checkout with no manifest to compare against (a tarball
# install), which is reported by name rather than obeyed silently — the
# swiftlint and cargo-machete precedent, and the same rule check_empty_scope.py
# holds every gate to.
goh_require_current() {
    local bin="$1" manifest="$GOH_ROOT/Cargo.toml" want got
    if [ ! -f "$manifest" ]; then
        warn "no $manifest — cannot tell whether $bin is current, and it may be skipping steps"
        return 0
    fi
    want="$(grep -m1 '^version = ' "$manifest" | cut -d'"' -f2)"
    got="$("$bin" --version 2>/dev/null | awk '{print $NF}')"
    if [ -n "$want" ] && [ "$got" = "$want" ]; then
        return 0
    fi
    err "the goh binary serving this gate is BEHIND the source: $bin reports ${got:-nothing},"
    err "  $manifest declares $want. A step added since that binary was built is NOT running, and"
    err "  a step that does not run prints exactly what a step that passes prints."
    err "  Rebuild it:  $GOH_ROOT/scripts/build-goh.sh    (or ./install.sh, which calls it)"
    return 1
}

# ── Native first. ───────────────────────────────────────────────────────────
# The `goh` binary carries this whole pipeline (native emoji / markers /
# length / secrets scanners; the remaining checkers delegated to the same
# Python files below), proven step-for-step identical by
# tests/test_goh_structural_parity.py. Resolution: $GOH_BIN, then the copy
# install.sh drops at bin/goh, then `goh` on PATH. Without one, the Python
# pipeline below runs and SAYS so once — a fallback that looks like the real
# thing is how interpreter drift stays invisible. GOH_NO_NATIVE=1 forces the
# Python path (the parity test uses it to drive this side).
_goh_dirty="$(_goh_dirty_gate_source)"
if [ -n "$_goh_dirty" ]; then
    warn "the gates about to judge this tree are NOT committed — a verdict from uncommitted"
    warn "  gate source is not reproducible from any commit. Every repo's hooks run these files:"
    printf '%s\n' "$_goh_dirty" | sed 's/^/    /' >&2
    warn "  … in the shared gates checkout at $GOH_ROOT, not in this repo. Commit or stash."
    warn "  scripts/build-goh.sh will not publish bin/goh from such a tree."
    unset _goh_dirty
fi

# shellcheck source=gates/_goh_bin.sh
. "$HERE/_goh_bin.sh"
goh_resolve_native
if [ -n "$goh_native" ]; then
    goh_require_current "$goh_native" || exit 1
    # Arguments were validated above; only the two accepted scopes reach here.
    if [ "$SCOPE" = "--staged" ]; then
        exec "$goh_native" structural --staged
    fi
    exec "$goh_native" structural --full
elif [ -n "$goh_native_why" ]; then
    echo "· $goh_native_why — running the Python checkers" >&2
fi

# Only --staged is a checker-level scope; --full (and no argument) mean
# unrestricted. The scope flag is forwarded to checkers ONLY when it is
# --staged — forwarding --full verbatim made every full run die on argparse.
FWD=""
[ "$SCOPE" = "--staged" ] && FWD="--staged"

# Config comes from the TARGET repo root, wherever the gate was invoked from.
#
# AND FROM NOWHERE ELSE. Every optional step below is decided by whether a
# `GOH_*` key is PRESENT, so a value this process merely INHERITED does not
# fail the gate -- it quietly adds a step to it, or exempts a path from one.
# Measured 2026-10-02: `push_gate.sh` sourced `.gatesrc` under `set -a`, so a
# push of one repo reached the export gate with that repo's real
# `GOH_SKILLS_ROOT`, `GOH_SKILLS_CORPUS` and `GOH_PYTHON_FORMATTED` in its
# environment; the pytest suite the export gate runs inherited all three while
# every fixture repo in it declared none, and 18 tests went red on a clean tree.
# `gates/push_gate.sh` no longer exports the file (that was the producer), and
# this is the consumer half: the keys are dropped from the environment first, so
# a key is configuration if and only if the repo's own file says so.
#
# The list is the keys this script READS below, spelled once. `GOH_DIR`,
# `GOH_BIN` and `GOH_NO_NATIVE` are deliberately NOT in it: they say which
# binary to run, not which checks to run, there is no file for them, and the
# native binary resolved above reads the file and nothing else -- so this list
# is also what makes the two tiers agree about configuration.
#
# A key the file DOES declare is set by the source below whatever the
# environment said, so a repo can still override a value set higher up.
_goh_config_keys="GOH_MAX_LINES GOH_LINE_EXCLUDE GOH_LINE_BASELINE GOH_LINE_UNBOUNDED
GOH_EXCLUDE GOH_ALLOW GOH_SKILLS_CORPUS GOH_SKILLS_ROOT GOH_SKILLS_MAX_WORDS
GOH_NO_HOME_PATHS GOH_NO_KILL_BY_NAME GOH_PYTHON_FORMATTED
GOH_STEP_TIMEOUT GOH_STEP_GRACE"
for _goh_key in $_goh_config_keys; do unset "$_goh_key" || true; done
unset _goh_key _goh_config_keys
GOH_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ -f "$GOH_REPO_ROOT/.gatesrc" ]; then
    # shellcheck source=/dev/null
    . "$GOH_REPO_ROOT/.gatesrc"
fi

goh_init "structural"

# Emoji are a failure state — only the Kare icon set is permitted.
# GOH_EXCLUDE (regex) exempts vendored/generated trees, same as the cap.
# GOH_ALLOW permits extra characters repo-wide (e.g. historical mentions of
# removed glyphs); keep it empty unless the repo genuinely needs it.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "no disallowed emoji (staged)" python3 "$CHECKS/check_no_emoji.py" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${GOH_ALLOW:+--allow "$GOH_ALLOW"}
else
    goh_step "no disallowed emoji" python3 "$CHECKS/check_no_emoji.py" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${GOH_ALLOW:+--allow "$GOH_ALLOW"}
fi

# A conflict marker that reaches a commit is a merge someone walked away from.
goh_step "no conflict markers" python3 "$CHECKS/check_no_conflict_markers.py" ${FWD:+"$FWD"}

# Version provenance: a `--version` flag carrying no commit. A number answers "is
# this current?"; only a commit answers "what am I actually running?". STATIC by
# design -- a gate that must run every repo's binary is a gate that gets skipped
# on exactly the repos it would catch. Opt-in by convention, never by an
# inherited env var, because that does not survive the hook chain.
#
# MIRRORED in crates/goh/src/steps.rs, which is what actually runs when the
# native binary is present (this script execs it). Keep the two in step --
# tests/test_goh_structural_parity.py compares them.
if [ -f "$GOH_REPO_ROOT/.gates-version-baseline.json" ]; then
    goh_step "version provenance" python3 "$CHECKS/check_version_provenance.py" . \
        --baseline "$GOH_REPO_ROOT/.gates-version-baseline.json"
fi

# Cross-file markdown links: a relative link must resolve to a file AND to an
# anchor that file actually has. Both halves fail silently — a link to a heading
# that does not exist renders fine and 404s only on click, and the anchor is not
# the heading (GitHub lowercases, drops punctuation, hyphenates spaces), so it
# must be derived rather than typed. app_updates, 2026-10-01: an audit had to
# hand-compute `#48-what-90-can-actually-do-measured` into another file in the
# same repo, and had already written a wrong one. Same GOH_EXCLUDE as the emoji
# scan, for vendored docs (ztools' camoufox-rs PROTOCOL.md carries upstream's own
# broken anchor, which is upstream's to fix).
#
# MIRRORED in crates/goh/src/steps.rs, which is what actually runs when the
# native binary is present (this script execs it).
goh_step "markdown links resolve" python3 "$CHECKS/check_md_links.py" \
    ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${FWD:+"$FWD"}

# A committed Cargo.lock must agree with the manifest it was generated from.
# app_updates, 2026-10-01: a release commit bumped `[workspace.package] version`
# to 1.36.0 and shipped Cargo.lock still saying 1.35.0 for all four crates.
# Nothing noticed because any `cargo build` silently rewrites the lockfile — the
# working tree heals on the next compile while the COMMIT, which is what gets
# published, stays wrong. `check_tag_version.py` reads Cargo.toml and never
# opens the lockfile: the same class as the tag incident, one file over. NOT
# gated on being a Rust repo — absence is a named non-run, never a silent pass,
# which is what check_empty_scope.py requires of it.
#
# MIRRORED in crates/goh/src/steps.rs.
goh_step "Cargo.lock matches its manifests" python3 "$CHECKS/check_lock_version.py"

# Python shape, decided by a DECLARED rule set rather than by whoever edited
# last. This repo had no ruff configuration at all, so `ruff format` applied its
# defaults and the checker's answer depended on ruff's version and on the
# directory it was launched from -- while no gate here ever ran it over this
# repository, so it reported 129 of 148 files unformatted and nobody saw it.
# pyproject.toml now declares line-length 100 (matching rustfmt.toml) and the
# tree is formatted once; this step is what keeps it that way.
#
# NOT gated on ruff's absence being a pass: check_python_formatted.py refuses to
# skip when ruff is missing, because a formatter that is not installed is a
# missing gate.
# Full scope ONLY, and the guard is explicit rather than inherited: a
# formatter's verdict is a property of the whole tree, so a pre-commit hook
# that reformatted the repository to satisfy one commit would be worse than one
# that waits. crates/goh/src/steps_delegated.rs::step_python_formatted carries the
# same rule, and tests/test_goh_structural_parity.py is what caught the two
# disagreeing when the shell side ran it in staged mode and the native side did
# not.
# OPT-IN via GOH_PYTHON_FORMATTED in .gatesrc, and full scope only -- a repo
# that has never declared a rule set should not learn one by going red.
if [ "$SCOPE" != "--staged" ] && [ -n "${GOH_PYTHON_FORMATTED:-}" ]; then
    goh_step "python is ruff-formatted" python3 "$CHECKS/check_python_formatted.py"
fi

# One cap, one name. Repos previously called this check_file_length,
# check_loc and check_file_size, with three different limits.
# Exemption semantics: GOH_EXCLUDE exempts vendored/generated paths from BOTH
# the emoji scan and the cap. GOH_LINE_EXCLUDE is ADDITIVE to the length check
# only — the length exemption is GOH_EXCLUDE ∪ GOH_LINE_EXCLUDE. Paths named
# only by LINE_EXCLUDE stay exempt from the cap but ARE scanned for emoji
# unless GOH_EXCLUDE separately covers them (no consumer ever relied on
# replacement; surveyed 2026-08-25).
GOH_EX="${GOH_EXCLUDE:-}"
if [ -n "${GOH_LINE_EXCLUDE:-}" ]; then
    GOH_EX="${GOH_EX:+${GOH_EX}|}${GOH_LINE_EXCLUDE}"
fi
if [ -n "${GOH_MAX_LINES:-}" ]; then
    goh_step "file length <= ${GOH_MAX_LINES}" \
        python3 "$CHECKS/check_file_length.py" --max "$GOH_MAX_LINES" \
        ${GOH_EX:+--exclude "$GOH_EX"} ${FWD:+"$FWD"}
else
    warn "file-length cap not set — add GOH_MAX_LINES to .gatesrc to enable it"
fi

# The two line-cap bounds below read a baseline and measure files, so at
# --staged they run INSIDE the index view (goh_index_view): the commit is what
# is judged, not the tree around it. Both run from the repo root (the view's
# root at --staged), never the caller's cwd: the ratchet's `[ -f ]` and the
# checkers' relative paths once resolved against a subdirectory and skipped
# the ratchet without a word.
goh_line_root="$GOH_REPO_ROOT"
if [ "$SCOPE" = "--staged" ] && [ -n "${GOH_LINE_BASELINE:-}" ]; then
    goh_index_view "$GOH_REPO_ROOT" || die "structural: the repo root is outside the repo?"
    goh_line_root="$GOH_INDEX_VIEW"
fi
case "${GOH_LINE_BASELINE:-}" in
    /*) goh_line_baseline_path="$GOH_LINE_BASELINE" ;;
    *)  goh_line_baseline_path="$goh_line_root/${GOH_LINE_BASELINE:-}" ;;
esac

# An exemption from the CAP is not an exemption from having any bound at all.
# GOH_LINE_EXCLUDE and the shrink-only ratchet are separate mechanisms with
# separate lists, and nothing compared them: monitor had a 619-line test file
# named in LINE_EXCLUDE and absent from its baseline, so it was bounded by
# nothing and no gate said a word. Only runs when the repo points
# GOH_LINE_BASELINE at its ratchet baseline; a repo without one is told the
# check is off rather than passed over in silence.
if [ -n "${GOH_MAX_LINES:-}" ] && [ -n "${GOH_LINE_BASELINE:-}" ]; then
    goh_step_in "$goh_line_root" "line-cap exemptions carry a ceiling" \
        python3 "$CHECKS/check_exclusion_has_ceiling.py" --max "$GOH_MAX_LINES" \
        --baseline "$GOH_LINE_BASELINE" \
        ${GOH_LINE_EXCLUDE:+--line-exclude "$GOH_LINE_EXCLUDE"} \
        ${GOH_LINE_UNBOUNDED:+--unbounded "$GOH_LINE_UNBOUNDED"}
elif [ -n "${GOH_LINE_EXCLUDE:-}" ]; then
    warn "GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not — an exempted file"
    warn "  is bounded by nothing. Point GOH_LINE_BASELINE at the ratchet baseline."
fi

# ...and the ceilings are ENFORCED here, not left to each repo's own gate
# script. Until 2026-09-14 only the "carries a ceiling" check above ran in
# the shared layer; the shrink-only ratchet over `wc -l` was wired per repo,
# so a repo that listed ceilings and never ran the ratchet was, again,
# bounded by nothing. Files named in the baseline may come down and may not
# grow past their number.
if [ -n "${GOH_LINE_BASELINE:-}" ] && [ -f "$goh_line_baseline_path" ]; then
    goh_step_in "$goh_line_root" "cap-exempt files within their ceilings" \
        python3 "$CHECKS/check_baseline_ratchet.py" --baseline "$GOH_LINE_BASELINE" \
        --current-from-command "python3 '$CHECKS/loc_of_baseline_files.py' '$GOH_LINE_BASELINE'"
fi

# An agent SKILLS corpus (~/.claude/skills and friends) is authored prose that
# nothing compiles, so its defects are silent: a SKILL.md with no frontmatter
# can never be triggered and looks exactly like one that works. Opt in per repo
# with GOH_SKILLS_CORPUS=1 in .gatesrc; auto-detecting would surprise any repo
# that merely SHIPS example skills. Runs at both scopes because a duplicate
# lesson title is only visible across the whole corpus, and 40 skills is fast.
# At --staged the whole corpus is still checked, but AS THE INDEX HOLDS IT
# (goh_index_view): the working tree is not what the commit records. A corpus
# OUTSIDE the repo is no part of the commit at all, so --staged skips it by
# name and --full reads it where it lies.
if [ -n "${GOH_SKILLS_CORPUS:-}" ]; then
    # The corpus can live OUTSIDE the repo (a machine-level asset such as
    # ~/.claude/skills), so its absence is a fact about the machine, not a
    # defect in the tree. A NAMED skip, never a silent one -- the swiftlint and
    # cargo-machete precedent. Absent this branch the gate hard-failed anywhere
    # HOME is not the developer's, which is exactly what goh's own self-host
    # test does: it commits a copy of this repo with HOME set to a temp dir, and
    # it exists to prove .gatesrc stays hermetic.
    goh_skills_root="${GOH_SKILLS_ROOT:-$GOH_REPO_ROOT}"
    if [ -d "$goh_skills_root" ]; then
        if [ "$SCOPE" != "--staged" ]; then
            goh_skills_view="$goh_skills_root"
        elif goh_index_view "$goh_skills_root"; then
            goh_skills_view="$GOH_INDEX_VIEW"
        else
            goh_skills_view=""
            warn "$goh_skills_root is outside this repo, so no part of this commit — NOT checked at --staged (--full reads it)"
        fi
        if [ -n "$goh_skills_view" ]; then
            goh_step "skills corpus" python3 "$CHECKS/check_skills_corpus.py" \
                --root "$goh_skills_view" \
                ${GOH_SKILLS_MAX_WORDS:+--max-words "$GOH_SKILLS_MAX_WORDS"}
        fi
    else
        warn "skills corpus not present at $goh_skills_root — NOT checked"
    fi
fi

# Bash is the most-edited language under these gates. bash -n always runs;
# the lint stage degrades to a named warning when shellcheck is not
# installed (swiftlint precedent), never a silent skip. Same GOH_EXCLUDE
# as the emoji scan.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "shell lint (staged)" bash "$CHECKS/check_shell_lint.sh" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
else
    goh_step "shell lint" bash "$CHECKS/check_shell_lint.sh" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
fi

# Committed secrets outrank every other defect class. Same GOH_EXCLUDE;
# revoked vectors suppress per-line with `secret-ok: <reason>`.
if [ "$SCOPE" = "--staged" ]; then
    goh_step "no committed secrets (staged)" python3 "$CHECKS/check_no_secrets.py" --staged \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
else
    goh_step "no committed secrets" python3 "$CHECKS/check_no_secrets.py" \
        ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
fi

# A hard-coded home path (/Users/<x>/…, ~/Projects/…, $HOME/Projects/…) in a
# shipped binary, script or doc works on one machine at one moment; the
# salary CLI failed for two weeks after its repos moved (2026-09). Opt in per
# repo with GOH_NO_HOME_PATHS=1 in .gatesrc once the tree is clean or its
# survivors carry `path-ok: <reason>`; landing it red everywhere at once is
# how a gate gets switched off. Same GOH_EXCLUDE as its siblings.
if [ -n "${GOH_NO_HOME_PATHS:-}" ]; then
    if [ "$SCOPE" = "--staged" ]; then
        goh_step "no hard-coded home paths (staged)" python3 "$CHECKS/check_no_home_paths.py" --staged \
            ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
    else
        goh_step "no hard-coded home paths" python3 "$CHECKS/check_no_home_paths.py" \
            ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
    fi
fi

# A process killed by NAME (pkill -f, killall, a pgrep feeding a kill) is
# every process with that name, whoever owns it: zinc's test cleanup ran
# `pkill -9 -f camoufox` and SIGKILLed another session's Gemini browser
# (2026-09-23). Kill by pid, process group, or the tree below your own pid.
# Opt in per repo with GOH_NO_KILL_BY_NAME=1 once kill_by_name_allow.json
# holds today's survivors with reasons, the same rollout as home paths.
if [ -n "${GOH_NO_KILL_BY_NAME:-}" ]; then
    if [ "$SCOPE" = "--staged" ]; then
        goh_step "no process kill by name (staged)" python3 "$CHECKS/check_no_kill_by_name.py" --staged \
            ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
    else
        goh_step "no process kill by name" python3 "$CHECKS/check_no_kill_by_name.py" \
            ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"}
    fi
fi

# A TEST that spawns a child nothing reaps on the panic path. media_server,
# 2026-10-03: a test spawned the real binary with `--bind 127.0.0.1:0` (a
# server that loops forever by design) and reaped it with an explicit
# `child.kill(); child.wait();` below four lines that can panic. Nine orphans
# accumulated, each holding the cargo build lock, so every LATER `cargo test`
# blocked -- and the leak itself was INVISIBLE, because the run that would
# have reported it was the run that had been killed. Run to completion the suite
# takes 0.22 s.
#
# The ordering rule is the point, and it is why this is not "is there a kill in
# the function": the reap EXISTED in that file. A failing test skips it.
# Same GOH_EXCLUDE as its siblings, so one line exempts a vendored tree.
#
# MIRRORED in crates/goh/src/steps_delegated.rs.
goh_step "no unreaped spawns in tests" python3 "$CHECKS/check_no_unreaped_spawn.py" \
    ${GOH_EXCLUDE:+--exclude "$GOH_EXCLUDE"} ${FWD:+"$FWD"}

# The companion question to the one below, and a different one: a self-proof shows a gate can
# fail on a VIOLATION; this shows it does not report compliance when its subject is ABSENT. Three
# factory gates had probes and still passed over an empty tree. Nobody edits a gate to break it --
# they rename the directory it reads, and a ratchet then reads a population that dropped to zero
# as every ceiling being met. Full scope only; it is a few seconds per repo because a blind gate
# bails fast.
if [ "$SCOPE" != "--staged" ]; then
    goh_step "gates refuse to pass over an empty tree" \
        python3 "$CHECKS/check_empty_scope.py"
fi

# A gate's self-proof is the only evidence it can fail, and until 2026-09-05 nothing ran one:
# twenty-four probes existed across the estate, all green, none executed by any gate or CI. A
# probe that is never run rots silently while the calibration ratchet goes on counting its gate
# as proven -- an unproven gate under a green light, which is worse than an honestly-missing one.
# Full scope only: it launches one subprocess per probe, which is a push-time cost, not a
# per-commit one.
if [ "$SCOPE" != "--staged" ]; then
    goh_step "gate self-proofs still pass" python3 "$CHECKS/check_probes_pass.py"
fi

# Disk hygiene used to run here on full scope. It no longer does: a 15s du
# stat-storm over host cache/scratch trees does not belong in a commit gate
# (2026-09-04: removed; the watch lives on as scripts/bin/disk_hygiene.sh in
# ~/Projects/scripts, unified with reclaim_build_space.sh which is the fix
# the watch names). Machine-full failures stay diagnosable; they just no
# longer block pushes.

goh_done
