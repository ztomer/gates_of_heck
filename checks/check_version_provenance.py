#!/usr/bin/env python3
"""A shipped binary should say WHICH build it is, not just which number.

A version number answers "is this current?" A commit answers "what am I
actually running?", which is the question you have when behaviour disagrees
with the tree you are reading. The common failure it prevents is mundane and
constant: an installed copy lagging the source, with no outward sign, because
the build lands in a shared target directory that nothing refreshes.

## Why this check is STATIC, and does not run anyone's binary

It would be easy to gate on `binary --version` actually printing a hash. That
gate cannot run on every repository: wrong architecture, missing toolchain, a
build that costs minutes, a crate that is a library with no binary at all. A
shared check that cannot run everywhere gets disabled or skipped on exactly
the repositories it would have caught, and a check that is skipped everywhere
is worse than no check -- it is a check that reads as protection.

So this asserts the **declaration** instead: that the version string references
a git hash and a build date, that something sets them, and -- because a name
can be a lie -- that a STALE BASELINE ENTRY is reported rather than believed.

## The rendered FORMAT is a contract, not this checker's job

`docs/contracts.md` (the `--version` format contract) owns the exact string a
build must produce. This check deliberately cannot see that string, and the
reason is the same one that made it static: seeing it would mean running every
consumer's binary. What it CAN see is the property behind the format -- a
provenance field is only credible if the script that sets it derives it from
something that MOVES (a commit), something that DATES (a clock), and something
that sees the working tree (an index hash or a dirtiness probe). HEAD alone
does not move when the tree goes dirty, which is why all three are required:
a version string that cannot tell a dirty build from a clean build of the same
commit is byte-identical to a lie.

## Scope, stated rather than implied

Only a version string this check can SEE is covered, and "can see" is now
`src/main.rs`, `src/bin/*.rs` AND `src/lib.rs` of every package that ships a
binary. `src/lib.rs` was missing: a clap derive on a struct in the library,
with a thin `main.rs` calling `parse()`, is the ordinary way to write this, and
the gap was invisible precisely because the declaring file was the one file
the checker never read. A package that is library-only is skipped, as before.

The consequence is ratcheted, not silent: `.gates-version-baseline.json` may
only SHRINK, and an entry naming a file that declares no version is a FINDING
(baseline 08cf7ac, entry `crates/cli/src/lib.rs` -- suppressed nothing while
looking like it suppressed something).

A project that computes its version at runtime from a file, or from an env var
supplied by a packaging system, is not matched by this and should not pretend
otherwise -- pass `--baseline` or exclude it explicitly rather than leaving it
silently unchecked.

Exit codes: 0 clean (or a named non-run), 1 findings, 2 a usage or
environment error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, foreign_repo_env, listed_files  # noqa: E402
from _rust_crates import (  # noqa: E402
    clause_is_backed,
    rust_binary_roots,
    rust_package_roots,
    sets_named_env,
)

# Anything that reads as a commit-ish provenance source in a version string.
HASH_ENV = re.compile(
    r"env!\(\s*\"[A-Z0-9_]*(GIT_HASH|GIT_SHA|COMMIT_SHA|COMMIT|BUILD_HASH|VCS_HASH|SOURCE_HASH)[A-Z0-9_]*\"\s*\)",
    re.IGNORECASE,
)
DATE_ENV = re.compile(
    r"env!\(\s*\"[A-Z0-9_]*(BUILD_DATE|BUILD_TIME|BUILD_TIMESTAMP|BUILT_AT|SOURCE_DATE)[A-Z0-9_]*\"\s*\)",
    re.IGNORECASE,
)
# ONE opaque field holding a whole clause the build script composes
# (`env!("APP_UPDATES_PROVENANCE")` is the live spelling). Its NAME claims
# nothing, so the claim moves one layer down to the script that sets it --
# see `clause_is_backed`.
CLAUSE_ENV = re.compile(r"env!\(\s*\"([A-Z0-9_]*PROVENANCE[A-Z0-9_]*)\"\s*\)", re.IGNORECASE)

# A version is declared INSIDE a `#[command(...)]` attribute, in either clap
# spelling: the bare shorthand `version,` (by far the common one) or
# `long_version = "..."`. The first version of this regex required an `=`, so it
# matched neither real spelling and reported a compliant clap binary as
# clean -- found by calibrating against a real fixture rather than a sketch.
COMMAND_ATTR = re.compile(r"#\s*\[\s*command\s*\((?P<body>.*?)\)\s*\]", re.DOTALL)
BARE_VERSION = re.compile(r"(?:^|,)\s*version\s*(?:,|$)")
LONG_VERSION = re.compile(r"\blong_version\s*=|\bversion\s*=\s*\"")

MAIN_FILES = ("src/main.rs", "src/lib.rs")


def provenance_problems(pkg: Path, text: str) -> list[str]:
    """Findings for one version-declaring file, in `pkg`."""
    clause = CLAUSE_ENV.search(text)
    has_hash = bool(HASH_ENV.search(text)) or clause is not None
    has_date = bool(DATE_ENV.search(text)) or clause is not None
    problems = []
    if not has_hash:
        problems.append(
            "declares a version but never embeds a commit hash "
            '(expected an env!("...GIT_HASH")-style field)'
        )
    if not has_date:
        problems.append(
            "declares a version but never embeds a build date "
            '(expected an env!("...BUILD_DATE")-style field)'
        )
    if not (has_hash and has_date):
        return problems
    # Named fields: the NAME is the claim, so the only thing left to verify is
    # that a build script actually emits one. An opaque clause field carries no
    # claim in its name, so it is held to the stronger test.
    if not clause:
        for pattern, kind in ((HASH_ENV, "commit hash"), (DATE_ENV, "build date")):
            name = pattern.search(text).group(0)
            name = re.search(r'"([^"]+)"', name).group(1)
            if not sets_named_env(pkg, name):
                problems.append(
                    f"the version string references {kind} but no build script "
                    f"sets it (expected a build.rs emitting cargo:rustc-env={name}=)"
                )
        return problems
    return clause_is_backed(pkg, clause.group(1))


def check(root: Path, allowlist: set[str], staged: bool = False):
    """(problems, examined, declaring).

    `examined` counts files that DECLARE a version. Not the files read: a file
    read but declaring nothing is correctly ignored and still leaves this gate
    with no population, which is the case main() must not report as a pass.
    `declaring` is that same set, and it is what makes a baseline entry that
    suppresses NOTHING visible as a finding.
    """
    problems: list[str] = []
    declaring: set[str] = set()
    # In staged scope, narrow to what the INDEX holds. The walk finds every
    # version-declaring file in the tree, and a pre-commit hook must judge the
    # commit being made -- otherwise an unstaged edit to an unrelated file
    # blocks a commit that does not contain it.
    scope: set[str] | None = None
    if staged:
        scope = {
            f for f in listed_files(str(root), staged=True) if f.endswith(".rs")
        }

    for path in rust_binary_roots(root, MAIN_FILES):
        if scope is not None and str(path.relative_to(root)) not in scope:
            continue
        rel = str(path.relative_to(root))
        # THE INDEX at --staged, never the worktree (contract #3). Narrowing the
        # scope without also reading the right bytes is half the fix: the file
        # list came from the index and the content came from the editor, so a
        # violation written after `git add` blocked a commit that did not
        # contain it, and a fix staged over a violation never took effect.
        blob = content_bytes(str(root), rel, staged=staged)
        if blob is None:
            continue
        text = blob.decode("utf-8", "replace")
        if not any(
            BARE_VERSION.search(b) or LONG_VERSION.search(b) for b in COMMAND_ATTR.findall(text)
        ):
            continue  # this binary does not declare a version; not our business
        declaring.add(rel)
        # Counted BEFORE the baseline is consulted. The baseline suppresses
        # FINDINGS, not population: a repo that has ratcheted its last offender
        # still has a declaring file, and counting it as "nothing examined" made
        # a fully-wound-down repo report itself not applicable -- which is the
        # opposite of the state it is in.
        if rel in allowlist or str(path) in allowlist:
            continue
        # The MOST SPECIFIC package, not the first one that matches. The roots
        # are sorted, and the repository root is itself one of them (it has a
        # Cargo.toml), so `path.is_relative_to(cand)` was true for the root on
        # every workspace member and every crate's build script was looked for
        # at the workspace top. `build.rs` lives at the PACKAGE root, so in a
        # workspace that search always came up empty and every finding was
        # "no build script sets it" -- true, and about the wrong directory.
        pkg = max(
            (c for c in rust_package_roots(root) if path.is_relative_to(c)),
            key=lambda c: len(c.parts),
            default=root,
        )
        for problem in provenance_problems(pkg, text):
            problems.append(f"{rel}: {problem}")

    # A baseline entry that matches nothing is not a suppression, it is a
    # CLAIM of one. Left alone it reads as debt paid while nothing was policed
    # (app_updates shipped exactly that: `crates/cli/src/lib.rs`, a file this
    # checker never read). Reported, it is a shrinking chore instead.
    #
    # SCOPED, and that is the whole fix. Judged against the STAGED population,
    # an entry whose file is merely absent from this commit reads as stale -- so
    # a pre-commit hook reported every entry stale on every commit and blocked
    # all of them, on a repo whose baseline was correct (app_updates,
    # 2026-10-01: two entries for `crates/backup/src/bin/*`, both real and both
    # suppressing a real missing build date). A stale entry is a claim about the
    # BASELINE, which is the same file at both scopes, so it is judged at both.
    for entry in sorted(allowlist):
        if entry in declaring:
            continue
        # At --staged, an entry that still suppresses something but whose file
        # is not part of THIS commit is neither judged nor called stale. Judged:
        # its file is not here, so there is nothing to judge. Called stale: the
        # baseline is the same file at both scopes, so a finding here would be a
        # claim about the baseline dressed as a claim about the commit.
        if staged and entry in version_declaring_everywhere(root):
            continue
        problems.append(
            f".gates-version-baseline.json: entry {entry} names no "
            f"version-declaring source file -- it suppresses nothing; "
            f"delete the entry"
        )
    return problems, len(declaring), declaring


def version_declaring_everywhere(root: Path) -> set[str]:
    """Every version-declaring source file in the TREE, at any scope.

    Read from the worktree, deliberately: this answers "is the baseline entry
    still suppressing something?", and the answer must not depend on what
    happens to be staged. Judging it from the index would make the same
    question have a different answer before and after a `git add`.
    """
    declaring: set[str] = set()
    for path in rust_binary_roots(root, MAIN_FILES):
        rel = str(path.relative_to(root))
        blob = content_bytes(str(root), rel, staged=False)
        if blob is None:
            continue
        text = blob.decode("utf-8", "replace")
        if any(
            BARE_VERSION.search(b) or LONG_VERSION.search(b) for b in COMMAND_ATTR.findall(text)
        ):
            declaring.add(rel)
    return declaring


CLAP_HEAD = '#[command(name = "app", {decl})]'
# One opaque field, composed from all three sources -- app_updates' build.rs
# reduced to the three lines the check reads. Spelled as that script spells
# them (a date from the clock or SOURCE_DATE_EPOCH, an index hash, a commit), so
# the probe cannot pass by accident on a substring this file happens to own.
CLAUSE_BUILD = (
    'println!("cargo:rustc-env=APP_PROVENANCE={}", clause(\n'
    '    git(&["rev-parse", "HEAD"]),\n'
    '    git(&["write-tree"]),\n'
    '    git(&["status", "--porcelain"]),\n'
    '    SystemTime::now(),\n'
    '));\n'
)
# The half that was silently optional: a commit and a date, and no question
# asked of the working tree.
NO_TREE_BUILD = (
    'println!("cargo:rustc-env=APP_PROVENANCE={}", SystemTime::now());\n'
)


def _scratch(root: Path, *args: str) -> None:
    """git on the probe's OWN fixture -- never the repo being gated (contract 12)."""
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                   env=foreign_repo_env())


def _fixture(root: Path, decl: str, build: str | None, lib: bool = False) -> None:
    """A one-crate repo whose binary declares its version with `decl`."""
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(
        '[package]\nname = "app"\nversion = "1.0.0"\n'
        + ('\n[[bin]]\nname = "app"\n' if lib else ""),
        encoding="utf-8",
    )
    (root / "src" / "lib.rs").write_text(
        "use clap::Parser;\n#[derive(Parser)]\n"
        + CLAP_HEAD.format(decl=decl)
        + "\npub struct Args;\n",
        encoding="utf-8",
    )
    (root / "src" / "main.rs").write_text("fn main() { let _ = app::Args; }\n", encoding="utf-8")
    if build is not None:
        (root / "build.rs").write_text(build, encoding="utf-8")
    _scratch(root, "init", "-q", "-b", "main")
    _scratch(root, "add", "-A")
    _scratch(root, "-c", "user.name=t", "-c", "user.email=t", "commit", "-qm", "c")


def probe() -> int:
    """Calibration as a gate: a version with no build behind it goes RED, a
    truthful one GREEN, and a lib.rs-declared version is REACHED.

    The lib case is the one worth carrying: it is the shape app_updates uses
    and the shape this checker used to be blind to, so "no findings" there
    would have been indistinguishable from "nothing to look at".
    """
    import tempfile

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        plain = Path(td) / "plain"
        _fixture(plain, "version,", CLAUSE_BUILD)
        red, examined, _ = check(plain, set(), False)
        lib = Path(td) / "lib"
        _fixture(lib, 'long_version = env!("APP_PROVENANCE"),', CLAUSE_BUILD, lib=True)
        in_lib, lib_examined, _ = check(lib, set(), False)
        # Reached, and READ: over this fixture the declaring file is lib.rs, so a
        # nonzero examined count is the only proof the walk got there at all.
        blind = Path(td) / "blind"
        _fixture(blind, 'long_version = env!("APP_PROVENANCE"),', NO_TREE_BUILD)
        opaque, _, _ = check(blind, set(), False)
        green = Path(td) / "green"
        _fixture(green, 'long_version = env!("APP_PROVENANCE"),', CLAUSE_BUILD)
        findings, _, _ = check(green, set(), False)
        # ...and the dead-entry half, which is what the widened scope exposed:
        # an entry naming a file this tree has but that declares no version.
        stale, _, _ = check(green, {"src/bin/gone.rs"}, False)

        for label, want, got in (
            ("a version with no commit and no date is RED",
             (2, 1), (len(red), examined)),
            ("a version declared in src/lib.rs is REACHED and is clean",
             (0, 1), (len(in_lib), lib_examined)),
            ("a clause that never asks about the working tree is RED",
             (1, 1), (len(opaque), 1)),
            ("a truthful version is GREEN", 0, len(findings)),
            ("a baseline entry naming an unread file is a FINDING",
             1, len(stale)),
        ):
            if want != got:
                print(f"✗ probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                print(f"✓ probe: {label}")

    if bad:
        print(f"✗ check_version_provenance --probe: {bad} case(s) wrong")
        return 1
    print("✓ check_version_provenance --probe: a bare version goes red, a truthful "
          "one green, lib.rs included")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    ap.add_argument(
        "--baseline",
        default=None,
        help="JSON file of paths known not to comply; entries are skipped and may shrink",
    )
    ap.add_argument(
        "--staged",
        action="store_true",
        help="examine only Added/Copied/Modified index entries (pre-commit scope)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument(
        "--probe",
        action="store_true",
        help="prove this gate can go red (discovered and run by check_probes_pass)",
    )
    args = ap.parse_args()

    if args.probe:
        return probe()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"✗ not a directory: {root}", file=sys.stderr)
        return 2

    allowlist: set[str] = set()
    if args.baseline and Path(args.baseline).is_file():
        try:
            allowlist = set(json.loads(Path(args.baseline).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"✗ cannot read baseline {args.baseline}: {e}", file=sys.stderr)
            return 2

    problems, examined, _ = check(root, allowlist, args.staged)
    if args.json:
        print(json.dumps({"findings": problems, "examined": examined}, indent=2))
        return 1 if problems else 0
    for p in problems:
        print(f"✗ [version_provenance] {p}")
    if problems:
        print(f"--- {len(problems)} finding(s) ---")
        return 1
    # Over ZERO examined files the sentence below is a lie: it claims every
    # declared version complies, having declared none. This gate's population is
    # the files that DECLARE a version, unlike check_no_emoji and friends whose
    # scan set IS the subject -- so an empty tree does not make it clean, it
    # makes it blind, and the empty-scope sweep would otherwise record it as a
    # gate that reports success over nothing.
    #
    # Not a failure: a repo with no Rust (or none reached by the baseline) has
    # nothing to police. A named non-run, stated as one.
    if examined == 0:
        print("⚠ [version_provenance] no version-declaring source files in scope "
              "— nothing examined (not applicable)")
        return 0
    print(f"✓ [version_provenance] all {examined} version-declaring source file(s) "
          "carry a commit and a build date")
    return 0


if __name__ == "__main__":
    sys.exit(main())