#!/usr/bin/env python3
"""A committed `Cargo.lock` must agree with the manifest it was generated from.

    Cargo.toml says 1.36.0   Cargo.lock says 1.35.0   ->  finding

## THE DEFECT, and why every existing gate missed it

app_updates, 2026-10-01. The release commit bumped `[workspace.package]
version` to 1.36.0 and touched nothing else. `Cargo.lock` shipped 1.35.0 for
all four workspace crates, because the release bumped only the manifest.

Nothing noticed, and the reason is structural rather than accidental:

* **cargo silently repairs it.** Any `cargo build` rewrites the lockfile from
  the manifest, so the working tree self-heals on the next compile. The defect
  only exists in the COMMIT -- which is exactly what gets published, and what
  `git clone` reproduces. A gate that runs `cargo` before looking has already
  destroyed its own evidence.
* **the commit gate is green anyway.** Nothing in the estate compared the two
  files, so a commit whose manifest and lockfile disagree is a normal commit.
* **the tag gate cannot see it.** `check_tag_version.py` compares a pushed tag
  against the version DECLARED at its commit, and the declaration said the
  right thing. It reads `Cargo.toml`; it never opens `Cargo.lock`. Same class,
  one file over: a name checked against something other than the artifact that
  claims it.

The class: **a generated artifact that makes a claim, checked against nothing.**
A lockfile is a claim about what was resolved and at what versions; a manifest
is a claim about what was asked for. They are the same claim written twice, so
one of them is going to be stale and there is no gate standing between them.

## WHAT IS COMPARED, and what is deliberately not

Two arms, both narrow on purpose:

1. **Every workspace member's own manifest against its lockfile entry.** This
   is the incident, exactly. `version.workspace = true` is resolved to
   `[workspace.package] version` first -- reading the literal string `true`
   would compare `true` to `1.36.0` and manufacture a finding on every
   inheriting member, which is most of a modern workspace.

2. **The declared release version, when one is discoverable.** The strategies
   are the SAME registry `check_tag_version.py` uses (`GOH_TAG_VERSION_SOURCES`),
   imported rather than copied, so a repo that declares its version somewhere
   else joins with config and not with a second implementation. The arm only
   fires where it is unambiguous: a member that INHERITS the workspace version,
   or a repo whose release version is declared in two places that disagree.

A workspace whose members deliberately carry DIFFERENT literal versions (five
crates at five numbers, no `[workspace.package]`) is a real layout, not a
defect, and is a named non-run rather than five findings.

Third-party entries are never compared: a registry crate's version in the
lockfile is a fact about upstream, not a claim this repo makes.

    check_lock_version.py             # this repo
    check_lock_version.py --json
    check_lock_version.py --probe    # prove this gate can go red

Exit codes: 0 clean (or a named non-run), 1 findings, 2 usage/environment.
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
from _gitutil import foreign_repo_env, repo_root  # noqa: E402

# One registry, shared with the tag checker: `file:VERSION` and
# `cargo:Cargo.toml` already know what a declaration looks like, and two
# implementations of "what a version declaration is" is how they drift.
from check_tag_version import DEFAULT_SOURCES, parse_sources  # noqa: E402

from _cargo_toml import parse_lock, parse_package, parse_workspace  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402


def member_manifests(root: Path, members: list[str]) -> list[Path]:
    """Manifest paths for the workspace members, globs expanded, skips in place.

    `crates/*` is the live spelling in every workspace here; a member list of
    literal paths must work too, and an unexpanded glob silently yielding
    nothing is how "this workspace has no members" gets believed.
    """
    if not members:
        return []
    out: list[Path] = []
    for pattern in members:
        if any(ch in pattern for ch in "*?["):
            out.extend(sorted(p for p in root.glob(pattern) if p.is_dir()))
        else:
            candidate = root / pattern
            if candidate.is_dir():
                out.append(candidate)
    return [p / "Cargo.toml" for p in out if (p / "Cargo.toml").is_file()]


def release_version(root: Path, spec: str) -> tuple[list[str], list[str]]:
    """([(label, version)], [labels present but declaring nothing]) read from the
    WORKING TREE.

    Deliberately not at a commit, unlike the tag checker: this is a check on
    the tree, and `cargo` will have rewritten the lockfile by the time any
    commit is made. Reading a commit here would compare two files nobody is
    about to ship.
    """
    found, present = [], []
    for src in parse_sources(spec):
        if any(ch in src.path for ch in "*?["):
            for path in sorted(root.glob(src.path)):
                if not path.is_file():
                    continue
                present.append(str(path.relative_to(root)))
                found += [
                    (str(path.relative_to(root)), v)
                    for _, v in src.extract(path.read_text(encoding="utf-8", errors="replace"))
                ]
            continue
        path = root / src.path
        if not path.is_file():
            continue
        present.append(src.path)
        found += [
            (src.path, v)
            for _, v in src.extract(path.read_text(encoding="utf-8", errors="replace"))
        ]
    return found, present


def audit(root: Path, spec: str):
    """(findings, examined, notes). `examined` counts member crates compared.

    `notes` carries named non-runs, so "compared nothing" is never reported as
    the same sentence as "compared everything and agreed".
    """
    findings: list[str] = []
    notes: list[str] = []
    lock = root / "Cargo.lock"
    if not lock.is_file():
        notes.append("no Cargo.lock in this repository — nothing to compare")
        return findings, 0, notes
    root_manifest = root / "Cargo.toml"
    if not root_manifest.is_file():
        notes.append("no Cargo.toml beside Cargo.lock — nothing to compare")
        return findings, 0, notes

    locked = parse_lock(lock.read_text(encoding="utf-8", errors="replace"))
    root_text = root_manifest.read_text(encoding="utf-8", errors="replace")
    workspace_version, members = parse_workspace(root_text)

    manifests = member_manifests(root, members)
    root_name, root_version = parse_package(root_text)
    if root_name and (root_version or workspace_version):
        manifests = [root_manifest] + manifests
    if not manifests:
        notes.append(
            "this manifest declares no package and no workspace members — nothing to compare"
        )
        return findings, 0, notes

    # {name: the version its manifest declares, inheritance resolved} and the
    # subset the lockfile disagrees with. Arm 2 needs the first to know the
    # manifest and the lock AGREE; arm 1 needs the second so it does not also
    # report the same crate twice.
    resolved: dict[str, str] = {}
    stale: set[str] = set()
    compared = 0
    for manifest in manifests:
        rel = str(manifest.relative_to(root))
        name, version = parse_package(manifest.read_text(encoding="utf-8", errors="replace"))
        if not name:
            notes.append(f"{rel}: no [package] name — not compared")
            continue
        if version is None:
            # `version.workspace = true`. Resolving it is the whole point: the
            # literal text here is `true`, and comparing that to `1.36.0`
            # invents a finding on every member of a modern workspace.
            if workspace_version is None:
                notes.append(
                    f"{rel}: inherits a version and no [workspace.package] "
                    "declares one — not compared"
                )
                continue
            version = workspace_version
        resolved[name] = version
        compared += 1
        entry = locked.get(name)
        if entry is None:
            findings.append(
                f"{rel}: declares {name} {version} but Cargo.lock has no entry for "
                f"{name} — the lockfile is missing a workspace member"
            )
            continue
        if entry["version"] != version:
            stale.add(name)
            findings.append(
                f"{rel}: {name} declares version {version} but Cargo.lock says "
                f"{entry['version']} — the lockfile is stale; cargo will rewrite it "
                f"on the next build, and this commit is what gets published"
            )

    # Arm 2: the DECLARED release version, from the same source registry the tag
    # checker uses. It has a job arm 1 cannot do, and one job it must not do.
    #
    # It CAN: catch a release number bumped in one place only. media_server's
    # shape -- a `VERSION` file AND 31 crate manifests -- is the case: bump
    # VERSION, forget the manifests, and the manifest and the lockfile agree
    # with each other on the OLD number while the repo's release claim says a
    # new one. Arm 1 is silent (manifest == lock), which is exactly the hole.
    #
    # It MUST NOT: fire merely because a workspace's crates carry different
    # numbers from one another. That is a layout, not a defect, and five
    # findings for it is how a gate gets `--no-verify`'d. So the arm only fires
    # where the manifest AGREES with the lockfile and BOTH disagree with the
    # release number -- one number, two layers, and the disagreement is about
    # the release, not about resolution.
    declared, _present = release_version(root, spec)
    numbers = sorted({v for _, v in declared})
    if len(numbers) > 1:
        said = ", ".join(f"{label} says {v}" for label, v in declared)
        findings.append(
            f"this repo declares {len(numbers)} different release versions ({said}) — "
            f"the release number is a claim with no single answer"
        )
    elif numbers:
        # A `cargo:` source is SELF-REFERENTIAL: the number it yields is the
        # workspace number arm 1 already compared every member against. Firing
        # on it would say "this repo's crates disagree with this repo's own
        # declared number" for any workspace that keeps a crate at its own
        # version -- `monitor` ships `multitop-vault` at 0.21.0 under
        # `[workspace.package] version = "0.51.0"`, which is deliberate. So arm
        # 2 reads only a source OUTSIDE the manifests.
        outside = [(label, v) for label, v in declared if not label.endswith("Cargo.toml")]
        if not outside:
            notes.append(
                "the only declared release version is the workspace's own; nothing outside "
                "the manifests declares a release number to compare the lockfile against"
            )
        else:
            release = outside[0][1]
            for manifest in manifests:
                name, version = parse_package(
                    manifest.read_text(encoding="utf-8", errors="replace")
                )
                if not name or name not in locked or name not in resolved:
                    continue
                # Arm 1 already named it: one stale lockfile is one defect, and a
                # gate that reports it four times reads as noise.
                if name in stale or locked[name]["version"] == release:
                    continue
                findings.append(
                    f"{outside[0][0]}: the declared release version is {release} but "
                    f"{name} declares and locks {locked[name]['version']} — the release "
                    f"number was bumped in one place only"
                )
    return findings, compared, notes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="repository to read (default: cwd's repo)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = parser.parse_args(argv)
    if args.probe:
        return probe()

    root = Path(args.root or repo_root() or ".").resolve()
    if not root.is_dir():
        err(f"[lock_version] not a directory: {root}")
        return 2
    spec = os.environ.get("GOH_TAG_VERSION_SOURCES") or " ".join(DEFAULT_SOURCES)

    try:
        findings, examined, notes = audit(root, spec)
    except ValueError as exc:  # an unknown GOH_TAG_VERSION_SOURCES kind: config, so exit 2
        err(f"[lock_version] {exc}")
        return 2

    if args.json:
        print(json.dumps({"findings": findings, "examined": examined, "notes": notes}, indent=2))
        return 1 if findings else 0
    for note in notes:
        info(f"[lock_version] {note}")
    for finding in findings:
        err(f"[lock_version] {finding}")
    if findings:
        err(f"--- {len(findings)} finding(s): Cargo.lock disagrees with the manifest ---")
        return 1
    if examined == 0:
        info("[lock_version] no workspace crate compared — not applicable")
        return 0
    ok(f"[lock_version] OK — {examined} workspace crate(s) agree between Cargo.toml and Cargo.lock")
    return 0


# ── the probe ────────────────────────────────────────────────────────────────

FIXTURE_WORKSPACE = """\
[workspace]
members = ["crates/core", "crates/cli"]

[workspace.package]
version = "{version}"
"""
FIXTURE_LOCK = """\
version = 4

[[package]]
name = "app-core"
version = "{version}"

[[package]]
name = "app-cli"
version = "{version}"

[[package]]
name = "serde"
version = "1.0.219"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""


def _workspace(root: Path, manifest_version: str, lock_version: str) -> None:
    """app_updates' 2026-10-01 shape: the manifest bumped, the lockfile did not.

    Both crates INHERIT, which is the part that has to be resolved rather than
    compared literally -- a checker that read `version.workspace = true` as a
    version would fail on every workspace in the estate, and be right about
    none of them.
    """
    (root / "crates" / "core").mkdir(parents=True, exist_ok=True)
    (root / "crates" / "cli").mkdir(parents=True, exist_ok=True)
    (root / "Cargo.toml").write_text(
        FIXTURE_WORKSPACE.format(version=manifest_version), encoding="utf-8"
    )
    (root / "Cargo.lock").write_text(FIXTURE_LOCK.format(version=lock_version), encoding="utf-8")
    for name in ("core", "cli"):
        (root / "crates" / name / "Cargo.toml").write_text(
            f'[package]\nname = "app-{name}"\nversion.workspace = true\n', encoding="utf-8"
        )


def probe() -> int:
    """The incident, its fix, and the two shapes it must NOT invent findings for."""
    import tempfile

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        stale = Path(td) / "stale"
        _workspace(stale, "1.36.0", "1.35.0")
        red, examined, _ = audit(stale, " ".join(DEFAULT_SOURCES))

        (stale / "Cargo.lock").write_text(FIXTURE_LOCK.format(version="1.36.0"), encoding="utf-8")
        green, _, _ = audit(stale, " ".join(DEFAULT_SOURCES))

        # A workspace whose members declare their own versions is a real layout.
        # Five findings for it would be how this gate gets switched off.
        mixed = Path(td) / "mixed"
        (mixed / "crates" / "a").mkdir(parents=True, exist_ok=True)
        (mixed / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["crates/a"]\n\n[workspace.package]\nversion = "2.0.0"\n',
            encoding="utf-8",
        )
        (mixed / "crates" / "a" / "Cargo.toml").write_text(
            '[package]\nname = "a"\nversion = "2.1.0"\n', encoding="utf-8"
        )
        (mixed / "Cargo.lock").write_text(
            '[[package]]\nname = "a"\nversion = "2.1.0"\n', encoding="utf-8"
        )
        mixed_findings, mixed_examined, mixed_notes = audit(mixed, " ".join(DEFAULT_SOURCES))

        # A repo with no lockfile is a named non-run, not a pass over nothing.
        bare = Path(td) / "bare"
        bare.mkdir()
        (bare / "Cargo.toml").write_text("[workspace]\nmembers = []\n", encoding="utf-8")
        none_findings, none_examined, none_notes = audit(bare, " ".join(DEFAULT_SOURCES))

        # The two parsing shapes that read as nothing, proven here rather than
        # only in the pytest file: a member list spread over four lines
        # (`divoom-control`) and an `exclude` below it (`monitor`). A greedy
        # match runs to the LAST `]` in the section and turns the excluded
        # directory into a member; a per-line one never finds the partner. Both
        # make a real workspace report nothing to compare, so neither can be
        # left to the test suite alone.
        multiline = Path(td) / "multiline"
        (multiline / "crates" / "a").mkdir(parents=True, exist_ok=True)
        (multiline / "Cargo.toml").write_text(
            '[workspace]\nmembers = [\n    "crates/a",\n]\nexclude = ["fuzz"]\n', encoding="utf-8"
        )
        (multiline / "crates" / "a" / "Cargo.toml").write_text(
            '[package]\nname = "a"\nversion = "1.0.0"\n', encoding="utf-8"
        )
        (multiline / "Cargo.lock").write_text(
            '[[package]]\nname = "a"\nversion = "1.0.0"\n', encoding="utf-8"
        )
        _shape_version, shape_members = parse_workspace(
            (multiline / "Cargo.toml").read_text(encoding="utf-8")
        )
        shape_findings, shape_examined, _ = audit(multiline, " ".join(DEFAULT_SOURCES))

        for label, want, got in (
            (
                "a lockfile older than its manifest is RED, once per crate",
                (True, 2),
                (bool(red), len(red)),
            ),
            (
                "the finding names BOTH versions",
                True,
                bool(red) and "1.36.0" in red[0] and "1.35.0" in red[0],
            ),
            ("both members are compared, inherited version resolved", 2, examined),
            ("regenerating the lockfile is GREEN", 0, len(green)),
            (
                "per-crate versions are a layout, not a defect",
                (0, 1),
                (len(mixed_findings), mixed_examined),
            ),
            (
                "...and the abstention is stated, not silent",
                True,
                any("nothing outside" in n for n in mixed_notes),
            ),
            (
                "no Cargo.lock is a named non-run",
                (0, 0, True),
                (len(none_findings), none_examined, bool(none_notes)),
            ),
            ("a member list spread over lines is read whole", ["crates/a"], shape_members),
            (
                "an exclude below the member list is not a member",
                (0, 1),
                (len(shape_findings), shape_examined),
            ),
        ):
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

    if bad:
        err(f"check_lock_version --probe: {bad} case(s) wrong")
        return 1
    ok(
        "check_lock_version --probe: a stale lockfile goes red, a regenerated one green, "
        "per-crate versions untouched"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
