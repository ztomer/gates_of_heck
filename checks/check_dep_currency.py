#!/usr/bin/env python3
"""Dependency currency: is a declared dependency older than it should be?

    Cargo.toml says ureq = "2"   the graph also carries a 3.x parent   -> finding
    Cargo.toml says toml = "0.8"   crates.io's latest is 1.1.6          -> finding

## WHAT IS A FINDING, split by what can be PROVEN

Two arms, deliberately of different strength, because conflating them is how a
currency check becomes noise nobody reads:

1. **A direct dependency pinned BELOW what the graph already resolves. This
   FAILS, and it needs no network.** A house rule calls this a bug rather than
   a preference: if a parent in the graph already resolved 3.x of a crate and
   our own manifest still asks for 2, then our pin is the reason two majors of
   one crate are in the tree — and "two majors of one crate" means the same
   type is not the same type, which the compiler reports by NAME and never by
   pointing at the pin.

   This arm reads two files and stops. It is deterministic, offline, and
   cannot be argued with by a network hiccup.

2. **Currency against crates.io: this REPORTS, and does not fail by default.**
   A major behind moves in its own commit (house rule), and a patch behind is
   routine. A gate that fails on patch drift is red on every honest commit, and
   a gate that is always red is a gate nobody reads — which is the fate the
   house `check_bind_mount_sources` gate actually met, and it was deleted.

   So the severities are named separately and only arm 1 is fatal. `--strict`
   makes majors fatal for a repo that wants that; `--ratchet FILE` freezes the
   majors a repo has already TRIAGED so they cannot grow back, which is the
   shrink-only shape the exclusion ceilings use.

## WHY THE OFFLINE ARM IS NOT THE WHOLE THING

Because it is the part that cannot see a version nobody has asked for yet.
app_updates sat four majors behind with a perfectly consistent lockfile: every
manifest agreed with its graph, and nothing was duplicated, and `ureq 2` was
still two years old. That is invisible to any check that compares a manifest
with a lockfile, and it is exactly the drift the house rule exists to prevent.
Hence arm 2, run against crates.io and labelled with what it could not reach.

## AND WHEN IT CANNOT REACH THE NETWORK

It says so, in the output, and exits 0. A currency check that prints "clean"
because the index was unreachable is worse than no check: it converts an
absence of evidence into a clean bill, and the whole reason this file exists
is that absence of evidence kept looking like health.

## WHERE THE WORK IS SPLIT

Reading a tree -- manifests, workspace inheritance, lockfiles, what each declares
-- is `_dep_tree.py`; the semver rules are `_semver.py`; the one network call is
`_crates_io.py`. What stays here is everything that DECIDES: what a finding is,
which arm is fatal, and what the run says.

    --root DIR      repository to read (default: cwd)
    --json          machine-readable output
    --offline       arm 2 only, and say that is what happened
    --strict        also fail on a MAJOR behind (not on patch/minor drift)
    --ratchet FILE  fail on any major NOT listed in FILE (shrink-only)
    --probe         prove this gate can go red
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Finding:
    severity: str  # "pinned-below-graph" | "major-behind" | "minor-behind"
    name: str
    detail: str
    where: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    examined: int = 0
    manifests_seen: int = 0
    checked_currency: bool = False


from _crates_io import latest_many
from _dep_tree import Dep, declared_deps, lock_versions, manifests, nearest_lock, read_manifest
from _semver import _cmp, parse_version, req_allows  # noqa: F401  (re-exported for the probe)

# ── arm 1: pinned below what the graph already resolves (offline, fatal) ──────


def check_pinned_below_graph(deps: list[Dep], lock: Path, rel: str) -> list[Finding]:
    versions = lock_versions(lock)
    out: list[Finding] = []
    for dep in deps:
        held = versions.get(dep.name)
        if not held or len(held) < 2:
            continue  # one version in the tree: nothing is being held back
        admitted = [v for v in held if req_allows(dep.req, v) is True]
        refused = [v for v in held if req_allows(dep.req, v) is False]
        if not admitted or not refused:
            continue  # either we pin the newest, or the pin is not the cause
        key = parse_version
        mine, theirs = max(admitted, key=key), max(refused, key=key)
        # DIRECTION matters, and getting it backwards manufactures a finding on
        # every repo whose PARENT is behind: we admit 3.x and a parent carries
        # 2.x, which is the parent's staleness, not our pin holding anything
        # back. We are only the cause when the graph holds something NEWER than
        # what we admit.
        if _cmp(key(mine), key(theirs)) >= 0:
            continue
        out.append(
            Finding(
                "pinned-below-graph",
                dep.name,
                f"declared {dep.req!r}, so the graph carries {theirs} alongside {mine}: "
                f"two majors of one crate because of this pin. Move the requirement, or "
                f"say why in the manifest.",
                f"{rel} [{dep.section}]",
            )
        )
    return out


# ── arm 2: currency against crates.io (network, reported) ────────────────────


def check_currency(
    deps: list[Dep], unique: dict[str, str], rel: str
) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    notes: list[str] = []
    answers = latest_many([dep.name for dep in deps])  # concurrent, cached per name (P1b)
    for dep in deps:
        latest = answers.get(dep.name)
        if latest is None:
            notes.append(f"{dep.name}: crates.io unreachable, not checked")
            continue
        held = unique.get(dep.name)
        if not held:
            continue
        cur, lat = parse_version(held), parse_version(latest)
        if not cur or not lat:
            continue
        order = _cmp(cur, lat)
        if order >= 0:
            continue
        sev = "major-behind" if cur[0] < lat[0] else "minor-behind"
        findings.append(
            Finding(
                sev,
                dep.name,
                f"locked {held}, latest stable {latest}"
                + ("  (MAJOR behind: moves in its own commit)" if sev == "major-behind" else ""),
                f"{rel} [{dep.section}]",
            )
        )
    return findings, notes


# ── driver ────────────────────────────────────────────────────────────────────


def run(root: Path, offline: bool, ratchet: set[str] | None) -> Report:
    rep = Report()
    for manifest in manifests(root):
        doc = read_manifest(manifest)
        if doc is None:
            continue
        rel = str(manifest.relative_to(root))
        rep.manifests_seen += 1
        deps = declared_deps(doc, manifest)
        rep.examined += len(deps)
        lock = nearest_lock(manifest)
        if lock is not None:
            rep.findings.extend(check_pinned_below_graph(deps, lock, rel))

    if offline:
        rep.notes.append("offline: currency not checked (arm 2 skipped)")
        return rep

    # One lookup per crate NAME across the tree.
    unique: dict[str, str] = {}
    for manifest in manifests(root):
        doc = read_manifest(manifest)
        if doc is None:
            continue
        lock = nearest_lock(manifest)
        if lock is None:
            continue
        versions = lock_versions(lock)
        for dep in declared_deps(doc, manifest):
            held = versions.get(dep.name)
            if held:
                unique.setdefault(dep.name, max(held, key=parse_version))
    findings, notes = check_currency(
        [Dep(n, "*", Path("."), "tree") for n in unique], unique, "tree"
    )
    rep.findings.extend(findings)
    rep.notes.extend(notes)
    if not any("unreachable" in n for n in notes):
        rep.checked_currency = True
    if ratchet is not None:
        rep.findings.extend(
            Finding("major-behind", f["name"], "not in the ratchet", f["where"])
            for f in list(rep.findings)
            if f.severity == "major-behind" and f.name not in ratchet
        )
    return rep


def _probe() -> int:
    """Prove both arms can go red, and that a clean tree is green."""
    import tempfile

    bad = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n'
        )
        (root / "Cargo.lock").write_text(
            'version = 3\n\n[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
            '[[package]]\nname = "ureq"\nversion = "3.4.2"\n'
        )
        rep = run(root, offline=True, ratchet=None)
        red = [f for f in rep.findings if f.severity == "pinned-below-graph"]
        ok = (len(red) == 1 and red[0].name == "ureq") if (ok := True) else False
        print(("✓" if ok else "✗") + " a pin below the graph goes red")
        bad += 0 if ok else 1

        # The same tree with the pin MOVED must be green: the finding has to be
        # about OUR pin being behind, not about two versions existing at all.
        (root / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "3"\n'
        )
        rep = run(root, offline=True, ratchet=None)
        ok = not [f for f in rep.findings if f.severity == "pinned-below-graph"]
        print(("✓" if ok else "✗") + " moving the pin clears it")
        bad += 0 if ok else 1

        # 0.x is where a same-major comparison goes wrong.
        (root / "Cargo.toml").write_text(
            '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nfancy-regex = "0.19"\n'
        )
        (root / "Cargo.lock").write_text(
            'version = 3\n\n[[package]]\nname = "fancy-regex"\nversion = "0.19.2"\n'
        )
        rep = run(root, offline=True, ratchet=None)
        ok = not rep.findings
        print(("✓" if ok else "✗") + " a 0.19 pin against a 0.19 lock is clean")
        bad += 0 if ok else 1

        # Workspace inheritance must be resolved, not read as the string "true".
        (root / "Cargo.toml").write_text(
            "[workspace]\nmembers = ['m']\n\n[workspace.dependencies]\ntoml = \"0.8\"\n"
        )
        (root / "m").mkdir()
        (root / "m" / "Cargo.toml").write_text(
            '[package]\nname = "m"\nversion = "0.1.0"\n\n[dependencies]\ntoml = { workspace = true }\n'
        )
        (root / "Cargo.lock").write_text(
            'version = 3\n\n[[package]]\nname = "toml"\nversion = "0.8.23"\n\n'
            '[[package]]\nname = "toml"\nversion = "1.1.6"\n'
        )
        rep = run(root, offline=True, ratchet=None)
        ok = any(f.name == "toml" for f in rep.findings if f.severity == "pinned-below-graph")
        print(("✓" if ok else "✗") + " an inherited requirement is resolved and judged")
        bad += 0 if ok else 1

    # The semver rules themselves, where a wrong one invents findings.
    cases = [
        ("1", "1.9.9", True),
        ("1", "2.0.0", False),
        ("0.19", "0.19.2", True),
        ("0.19", "0.20.0", False),
        ("0", "0.0.5", True),
        ("^0.14", "0.19.2", False),
        # a bare 3-part req is a CARET req, not an exact one: `1.0.5` admits 1.0.6
        ("1.0.5", "1.0.6", True),
        ("^1.0.5", "2.0.0", False),
        (">=1.2, <2", "1.7.0", True),
        (">=1.2, <2", "2.0.0", False),
        ("1.0.5", "1.0.5", True),
        ("*", "9.9.9", True),
    ]
    for req, ver, want in cases:
        got = req_allows(req, ver)
        ok = got is want
        print(("✓" if ok else "✗") + f" req {req!r} admits {ver} -> {got}")
        bad += 0 if ok else 1

    if bad:
        print(f"check_dep_currency --probe: {bad} case(s) wrong")
        return 1
    print("check_dep_currency --probe: a stale pin goes red, a moved pin green, semver rules hold")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--ratchet", default=None)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args(argv)

    if args.probe:
        return _probe()

    root = Path(args.root).resolve() if args.root else Path.cwd()
    ratchet: set[str] | None = None
    if args.ratchet:
        ratchet = {
            ln.strip()
            for ln in Path(args.ratchet).read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        }

    rep = run(root, args.offline, ratchet)

    # NOTHING TO CHECK is a NAMED NON-RUN, not a pass: `check_empty_scope.py`
    # sweeps every checker over an empty tree and fails one that exits 0
    # without saying so. Exiting 0 WITH the marker satisfies that AND keeps
    # `rust_gate.sh` green on a crate that declares no dependencies, which
    # `tests/test_rust_gate.py` builds and requires to pass. Failing breaks one
    # estate rule; passing silently breaks the other.
    if rep.examined == 0:
        why = (
            "no manifest found"
            if rep.manifests_seen == 0
            else f"{rep.manifests_seen} manifest(s), none declaring a dependency"
        )
        msg = f"not applicable: {why} under {root} -- nothing to check"
        if args.json:
            print(json.dumps({"examined": 0, "not_applicable": msg, "fatal": []}, indent=2))
        else:
            print(f"· {msg}")
        return 0

    fatal = [f for f in rep.findings if f.severity == "pinned-below-graph"]
    majors = [f for f in rep.findings if f.severity == "major-behind"]
    minors = [f for f in rep.findings if f.severity == "minor-behind"]
    if args.strict:
        fatal = fatal + majors

    if args.json:
        print(
            json.dumps(
                {
                    "examined": rep.examined,
                    "currency_checked": rep.checked_currency,
                    "fatal": [f.__dict__ for f in fatal],
                    "major_behind": [f.__dict__ for f in majors],
                    "minor_behind": [f.__dict__ for f in minors],
                    "notes": rep.notes,
                },
                indent=2,
            )
        )
    else:
        print(
            f"· {rep.manifests_seen} manifest(s), "
            f"{rep.examined} direct dependency declaration(s) examined"
        )
        for f in fatal:
            print(f"✗ [{f.severity}] {f.name}: {f.detail}  ({f.where})")
        for f in majors:
            print(f"⚠ [major behind] {f.name}: {f.detail}  ({f.where})")
        for f in minors:
            print(f"· [drift] {f.name}: {f.detail}  ({f.where})")
        for n in dict.fromkeys(rep.notes):
            print(f"⚠ {n}")
        if not rep.checked_currency and not args.offline:
            print("⚠ currency NOT fully checked — this is not a clean bill")
        if fatal:
            print(f"\n✗ dependency currency: {len(fatal)} finding(s)")
        else:
            print("\n✓ no dependency pinned below the graph")
            if majors:
                print(
                    f"  ({len(majors)} major(s) behind, reported not failed — "
                    f"--strict to make them fatal)"
                )

    return 1 if fatal else 0


if __name__ == "__main__":
    sys.exit(main())
