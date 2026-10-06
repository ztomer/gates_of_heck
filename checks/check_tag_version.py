#!/usr/bin/env python3
"""A pushed `refs/tags/v*` tag must name the version its OWN COMMIT declares.

    refs/heads/main  abc123…  refs/heads/main  000000…
    refs/tags/v1.79.3 de3a31…  refs/tags/v1.79.3  000000…

## THE DEFECT, and why the existing gates missed it

media_server, 2026-10-01. A version was bumped, then `git commit --amend
--no-edit` ran twice and was REJECTED by the pre-commit hook twice — with
`2>/dev/null` swallowing the refusal, so the amend looked like it had worked.
`git tag -f v1.79.3` then named commit `a266067`, whose VERSION file and all
31 crate manifests still said 1.79.1, and `git push --follow-tags` published
it. Anyone checking out `v1.79.3` got a build that reported itself as 1.79.1.

The repo's own test (`test_native_version_flag`) DID catch the version
mismatch. It ran in the pre-push gate — but the push carried a TAG, and nothing
tied the version assertion to the ref being pushed. A version check that
cannot see a tag cannot stop a tag from lying. That is the class: **a name that
makes a claim, checked against something other than the object it names.**

Three properties follow, and each is a place the obvious implementation is wrong:

* **Read the refs being PUSHED, not all tags.** A pre-push hook is handed its
  refs on stdin. Scanning every tag in the repo makes one stale tag block
  every unrelated push until somebody deletes it — a gate that cries wolf is a
  gate that gets `--no-verify`'d.
* **Read the version at the COMMIT, never the working tree.** The working tree
  is not the thing being published; it may hold edits the amend is about to
  lose, or fixes the commit never got.
* **A tag whose version source is ABSENT is a FINDING, not a pass.** `v2.0.0`
  on a tree that declares no version is unverifiable, and "unverifiable" read
  as "fine" is how the next one ships.

## VERSION SOURCES are a list, not a hardcoded repo

Two layouts are live in this estate — a `VERSION` file (media_server) and a
workspace `version = "…"` in `Cargo.toml` (app_updates) — so the strategies
are a table. `GOH_TAG_VERSION_SOURCES` in `.gatesrc` adds or replaces paths
(space-separated `kind:path`), which is how a repo declaring its version
somewhere else joins without a change here.

    python3 checks/check_tag_version.py < refs.txt     # the hook's protocol
    python3 checks/check_tag_version.py --refs-file refs.txt --root .
    python3 checks/check_tag_version.py --probe        # prove this can go red

Exit codes: 0 clean (or a named non-run), 1 findings, 2 usage/environment.
"""

from __future__ import annotations  # OS python3 may be 3.9

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import os
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env, repo_root  # noqa: E402
from _version_sources import (  # noqa: E402
    DEFAULT_SOURCES,
    KINDS,
    STRATEGIES,
    _norm,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402

ZERO_SHA = "0" * 40

# Only refs that CLAIM a semver release. `refs/tags/nightly`, `v1` and
# `v1.2` are not release names and carry no version claim to check; a
# two-component tag is cut from a branch whose own declaration governs.
TAG_RE = re.compile(r"^refs/tags/v(?P<ver>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)$")


class Source:
    """One place a version is declared, and how to read it.

    `path` may be a GLOB, expanded against the TREE AT THE COMMIT (`git
    ls-tree`), never the working tree. Globbing is opt-in and the default list
    uses exact paths on purpose: a bare `**/Cargo.toml` sweeps `vendor/`, and
    a vendored crate's own version (app_updates' camoufox-rs = 0.1.0) is not
    this repo's release number. Scope the glob to what the repo ships —
    `cargo:crates/*/Cargo.toml` reaches media_server's 31 manifests.
    """

    def __init__(self, kind: str, path: str) -> None:
        self.kind = kind
        self.path = path
        self.extract = STRATEGIES[kind]

    @property
    def label(self) -> str:
        return f"{self.path} [{self.kind}]"


def parse_sources(spec: str) -> list[Source]:
    """`kind:path` entries, whitespace-separated. Unknown kind is exit 2."""
    out = []
    for item in spec.split():
        kind, sep, path = item.partition(":")
        if not sep or kind not in STRATEGIES:
            raise ValueError(
                f"unknown version source {item!r} (expected kind:path, kind in "
                + "/".join(KINDS)
                + ")"
            )
        out.append(Source(kind, path))
    return out


def _git(root: str, *args: str):
    """git on the repo being GATED — never a foreign one, so the hook's
    GIT_DIR/GIT_INDEX_FILE stay: this is the repository being published."""
    return subprocess.run(["git", "-C", root, *args], capture_output=True)


def _blob(root: str, commit: str, path: str) -> bytes | None:
    """The bytes of `path` AT `commit`. None means absent-or-unreadable."""
    out = _git(root, "show", f"{commit}:{path}")
    return out.stdout if out.returncode == 0 else None


def _paths_at(root: str, commit: str) -> list[str]:
    """Every path in the tree at `commit`. The worktree is not consulted."""
    out = _git(root, "ls-tree", "-r", "--name-only", commit)
    if out.returncode != 0:
        return []
    return out.stdout.decode("utf-8", "replace").splitlines()


def declared_at(root: str, commit: str, sources: list[Source]):
    """([(label, version)], [labels present but declaring nothing]).

    The second list is what makes "the file exists but says nothing" visible
    rather than indistinguishable from "the file is absent".

    A glob matching NOTHING is reported rather than skipped, so a typo'd
    `cargo:crate/*/Cargo.toml` cannot quietly retire the strategy.
    """
    found, present = [], []
    tree: list[str] | None = None
    for src in sources:
        targets = [src.path]
        if any(ch in src.path for ch in "*?["):
            tree = tree if tree is not None else _paths_at(root, commit)
            targets = [p for p in tree if fnmatch(p, src.path)]
        if not targets:
            present.append(f"{src.label} (matched no path)")
            continue
        for target in targets:
            blob = _blob(root, commit, target)
            if blob is None:
                continue
            present.append(target)
            for table, version in src.extract(blob.decode("utf-8", "replace")):
                found.append((f"{target} [{src.kind}] {table}", version))
    return found, present


def parse_refs(lines) -> list[tuple[str, str]]:
    """[(ref, sha)] from pre-push stdin. Malformed lines are skipped, not fatal."""
    return [(p[0], p[1]) for p in (line.split() for line in lines) if len(p) >= 2]


def audit(root: str, refs, sources: list[Source]):
    """(findings, examined). `examined` counts version tags actually checked."""
    findings: list[str] = []
    examined = 0
    for ref, sha in refs:
        m = TAG_RE.match(ref)
        if not m or sha == ZERO_SHA:
            continue  # not a release name, or a delete: nothing to claim
        expected = m.group("ver")
        examined += 1
        # A tag's sha is the TAG OBJECT; the claim is about the commit it
        # resolves to. `^{commit}` peels an annotated tag (requirement 4) and
        # is a no-op on a lightweight one.
        rev = _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        if rev.returncode != 0:
            findings.append(
                f"{ref}: local sha {sha[:12]} does not resolve to a commit — "
                "cannot verify what this tag points at"
            )
            continue
        commit = rev.stdout.decode().strip()
        found, present = declared_at(root, commit, sources)
        short = commit[:12]
        if not found:
            looked = ", ".join(s.label for s in sources)
            findings.append(
                f"{ref} -> {short}: NO version source declares a version at this "
                f"commit (looked for {looked}"
                + (f"; present but empty: {', '.join(present)}" if present else "")
                + ") — the tag names a version nothing in the tree declares"
            )
            continue
        wrong = [(label, ver) for label, ver in found if _norm(ver) != _norm(expected)]
        if wrong:
            said = ", ".join(f"{label} says {_norm(ver)}" for label, ver in wrong)
            findings.append(
                f"{ref} -> {short}: tag says {expected} but {said} — anyone "
                f"checking out this tag gets a build that reports itself "
                f"differently"
            )
    return findings, examined


def read_ref_stream(stream) -> list[str]:
    """The hook's stdin — unless it is a terminal.

    A terminal means nobody piped refs in: reading it would block a gate that
    is also swept by `check_empty_scope`, whose child inherits this process's
    stdin. Returning nothing there is a named non-run, not a silent pass.
    """
    try:
        if stream.isatty():
            return []
    except (AttributeError, ValueError):
        return []
    return stream.read().splitlines()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--refs-file", default=None, help="file of pre-push ref lines ('-' for stdin, the default)"
    )
    ap.add_argument("--root", default=None, help="repository to read (default: cwd's repo)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)

    if args.probe:
        return probe()

    root = args.root or repo_root()
    if not root or not os.path.isdir(root):
        err("[tag_version] not a git repository — pass --root")
        return 2

    spec = os.environ.get("GOH_TAG_VERSION_SOURCES") or " ".join(DEFAULT_SOURCES)
    try:
        sources = parse_sources(spec)
    except ValueError as e:
        err(f"[tag_version] {e}")
        return 2
    if not sources:
        err("[tag_version] GOH_TAG_VERSION_SOURCES names no source — refusing to pass over nothing")
        return 2

    if args.refs_file in (None, "-"):
        lines = read_ref_stream(sys.stdin)
    else:
        try:
            with open(args.refs_file, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError as e:
            err(f"[tag_version] cannot read refs file {args.refs_file}: {e}")
            return 2

    findings, examined = audit(root, parse_refs(lines), sources)

    if args.json:
        import json

        print(json.dumps({"findings": findings, "examined": examined}, indent=2))
        return 1 if findings else 0

    for f in findings:
        err(f"[tag_version] {f}")
    if findings:
        err(
            f"--- {len(findings)} finding(s): a tag you are pushing does not match "
            "what its own commit declares ---"
        )
        return 1
    if examined == 0:
        # Zero release tags in this push is a REAL state — a branch push, a
        # nightly — and "nothing examined" is the honest answer. It must say so:
        # a silent 0 here is indistinguishable from having checked the tag and
        # found it clean.
        info("[tag_version] no refs/tags/v<semver> in this push — not applicable")
        return 0
    ok(
        f"[tag_version] OK — {examined} pushed release tag(s) match the version "
        "declared at their own commit"
    )
    return 0


def _scratch(root: Path, *args: str, text: bool = False) -> str:
    """git on a PROBE's own fixture repo — never the repo being gated (contract 12).

    Called from a hook this inherits the pushing checkout's GIT_DIR, and a
    `git init`/`commit` under a linked worktree then re-initialises the REAL
    repository. Returns stdout when `text`.
    """
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=foreign_repo_env(),
        **({"text": True} if text else {}),
    )
    return out.stdout.strip() if text else ""


def _fixture_repo(root: Path, version: str) -> str:
    """A repo shaped like media_server: a VERSION file AND crate manifests.

    Both carry the same number, because the incident had both and a fixture
    with only one would let a gate that reads only the other look correct.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "Cargo.toml").write_text(
        f'[workspace]\nmembers = ["crates/healthcheck-rs"]\n\n'
        f'[workspace.package]\nversion = "{version}"\n',
        encoding="utf-8",
    )
    crate = root / "crates" / "healthcheck-rs"
    crate.mkdir(parents=True, exist_ok=True)
    (crate / "Cargo.toml").write_text(
        f'[package]\nname = "healthcheck"\nversion = "{version}"\n', encoding="utf-8"
    )
    _scratch(root, "init", "-q", "-b", "main")
    _scratch(root, "add", "-A")
    _scratch(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    return str(root)


def probe() -> int:
    """The calibration, run as a gate: red on the real defect, green on the fix.

    The red case is media_server a266067's shape exactly — tag `v1.79.3`,
    commit declaring 1.79.1 in a VERSION file and in a crate manifest.
    """
    import tempfile

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "media"
        _fixture_repo(repo, "1.79.1")
        # The incident's tag was ANNOTATED, so the ref's sha is a tag OBJECT,
        # not a commit. Peel it or the check never sees a VERSION file at all.
        _scratch(repo, "tag", "-a", "v1.79.3", "-m", "release")
        tag_obj = _scratch(repo, "rev-parse", "v1.79.3", text=True)
        refs = [f"refs/tags/v1.79.3 {tag_obj} refs/tags/v1.79.3 {ZERO_SHA}"]
        sources = parse_sources(" ".join(DEFAULT_SOURCES))

        red, examined = audit(str(repo), parse_refs(refs), sources)
        # A DIRTY working tree saying the right thing must not rescue it.
        (repo / "VERSION").write_text("1.79.3\n", encoding="utf-8")
        still_red, _ = audit(str(repo), parse_refs(refs), sources)
        green, _ = audit(str(repo), [], sources)
        absent, _ = audit(str(repo), [("refs/tags/v9.9.9", ZERO_SHA)], sources)

        for label, want, got in (
            ("a v-tag whose commit declares another version is RED", True, len(red) == 1),
            (
                "the finding names BOTH versions",
                True,
                bool(red) and "1.79.3" in red[0] and "1.79.1" in red[0],
            ),
            ("the finding names the source it read", True, bool(red) and "VERSION" in red[0]),
            ("the working tree is NOT the thing read", True, len(still_red) == 1),
            ("the same repo with no pushed tag is clean", (True, 0), (not green, len(green))),
            ("a ref this gate does not police never counts as examined", 0, examined - 1),
        ):
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        # And green once the commit itself is bumped — the whole point.
        _scratch(repo, "tag", "-d", "v1.79.3")
        for rel, text in (
            ("VERSION", "1.79.3\n"),
            (
                "Cargo.toml",
                '[workspace]\nmembers = ["crates/healthcheck-rs"]\n\n'
                '[workspace.package]\nversion = "1.79.3"\n',
            ),
            (
                "crates/healthcheck-rs/Cargo.toml",
                '[package]\nname = "healthcheck"\nversion = "1.79.3"\n',
            ),
        ):
            (repo / rel).write_text(text, encoding="utf-8")
        _scratch(repo, "add", "-A")
        _scratch(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bump")
        _scratch(repo, "tag", "v1.79.3")
        fixed = _scratch(repo, "rev-parse", "v1.79.3", text=True)
        fixed_refs = [f"refs/tags/v1.79.3 {fixed} refs/tags/v1.79.3 {ZERO_SHA}"]
        findings, examined = audit(str(repo), parse_refs(fixed_refs), sources)
        if findings or examined != 1:
            err(
                f"probe: a matching tag/commit pair is GREEN (got {findings!r}, "
                f"examined={examined})"
            )
            bad += 1
        else:
            ok("probe: a matching tag/commit pair is GREEN")

        # A tag with NO version source anywhere is unverifiable, and
        # unverifiable read as fine is the next way this ships.
        bare = Path(td) / "bare"
        bare.mkdir()
        # --allow-empty: a repo declaring no version has nothing to commit, and
        # `git commit` refusing an empty tree is not the case under test.
        _scratch(bare, "init", "-q", "-b", "main")
        _scratch(bare, "add", "-A")
        _scratch(
            bare, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c", "--allow-empty"
        )
        _scratch(bare, "tag", "v1.0.0")
        bare_sha = _scratch(bare, "rev-parse", "v1.0.0", text=True)
        bare_refs = parse_refs([f"refs/tags/v1.0.0 {bare_sha} refs/tags/v1.0.0 {ZERO_SHA}"])
        missing, _ = audit(str(bare), bare_refs, sources)
        if len(missing) != 1 or "NO version source" not in missing[0]:
            err(f"probe: a tag with no version source is a FINDING (got {missing!r})")
            bad += 1
        else:
            ok("probe: a tag with no version source is a FINDING, not a silent pass")

        # The glob reaches media_server's 31 crate manifests — the sibling
        # declaration of the same number — so a workspace that bumps
        # `[workspace.package]` and misses a member is caught too.
        globbed = parse_sources("cargo:crates/*/Cargo.toml")
        hit, _ = declared_at(str(repo), fixed, globbed)
        if len(hit) != 1 or "healthcheck-rs" not in hit[0][0] or _norm(hit[0][1]) != "1.79.3":
            err(f"probe: a scoped glob reaches the crate manifests (got {hit!r})")
            bad += 1
        else:
            ok("probe: a scoped glob reaches the crate manifests")

        # ...and one that matches nothing is reported, not skipped: a typo'd
        # glob must not retire a strategy in silence.
        typo = parse_sources("cargo:crate/*/Cargo.toml")
        found_typo, present_typo = declared_at(str(repo), fixed, typo)
        if found_typo or not any("matched no path" in p for p in present_typo):
            err(
                f"probe: a glob matching nothing is reported "
                f"(found={found_typo!r}, present={present_typo!r})"
            )
            bad += 1
        else:
            ok("probe: a glob matching nothing is reported, not skipped")

    if bad:
        err(f"check_tag_version --probe: {bad} case(s) wrong")
        return 1
    ok(
        "check_tag_version --probe: a lying tag goes red, a truthful one green, "
        "an unverifiable one is a finding"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
