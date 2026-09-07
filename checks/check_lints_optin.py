#!/usr/bin/env python3
"""Fail when a crate is silently exempt from its workspace's lint policy.

    check_lints_optin.py              # every workspace under the repo root
    check_lints_optin.py --staged     # only when a Cargo.toml is staged
    check_lints_optin.py --self-test  # prove this gate can go red

WHY. `[workspace.lints]` is a DECLARATION, not an application. A member crate
inherits it only if that crate's own Cargo.toml says

    [lints]
    workspace = true

and a crate that never says it inherits NOTHING. There is no warning: cargo
does not mention the omission, clippy reports what the crate opted into, and
the gate that runs `clippy -- -D warnings` passes because the crate genuinely
has no findings AT THE LEVELS IT IS SUBJECT TO.

Measured on `monitor`, 2026-09-07. Its workspace had declared `pedantic` and
`nursery` since the beginning and two of the three crates opted in. The third
-- the biggest, the one uploaded to every monitored host -- had no `[lints]`
table at all, and had accumulated 254 findings nobody had seen. Every gate was
green the whole time, because the policy was true of the workspace file and not
of the code.

WHAT THIS DOES NOT CHECK. Whether the policy is any good, or whether a crate
outside a workspace has one. Those are judgement; this is the mechanical half:
if a workspace states a policy, every member is subject to it.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import repo_root  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok  # noqa: E402

WORKSPACE_LINTS = re.compile(r"^\s*\[workspace\.lints(\.\w+)?\]", re.M)
LINTS_TABLE = re.compile(r"^\s*\[lints(\.\w+)?\]", re.M)
LINTS_WORKSPACE = re.compile(r"^\s*\[lints\]\s*$.*?^\s*workspace\s*=\s*true", re.M | re.S)
MEMBERS = re.compile(r"^\s*members\s*=\s*\[(.*?)\]", re.M | re.S)
EXCLUDE = re.compile(r"^\s*exclude\s*=\s*\[(.*?)\]", re.M | re.S)


def _entries(match) -> list[str]:
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def audit(root: Path) -> tuple[list[str], int]:
    """(findings, crates inspected) for every workspace under `root`."""
    findings: list[str] = []
    inspected = 0
    for manifest in sorted(root.rglob("Cargo.toml")):
        if "target" in manifest.parts or "references" in manifest.parts:
            continue
        text = manifest.read_text(errors="replace")
        if not WORKSPACE_LINTS.search(text):
            continue
        base = manifest.parent
        excluded = set(_entries(EXCLUDE.search(text)))
        for member in _entries(MEMBERS.search(text)):
            if member in excluded:
                continue
            member_manifest = base / member / "Cargo.toml"
            if not member_manifest.exists():
                continue
            inspected += 1
            body = member_manifest.read_text(errors="replace")
            if LINTS_WORKSPACE.search(body):
                continue
            rel = member_manifest.relative_to(root)
            if LINTS_TABLE.search(body):
                findings.append(
                    f"{rel}: has its own [lints] table but does not inherit the "
                    f"workspace policy ([lints] workspace = true)"
                )
            else:
                findings.append(
                    f"{rel}: no [lints] table — this crate inherits NONE of the "
                    f"workspace's declared lints"
                )
    return findings, inspected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    root = Path(repo_root())
    findings, inspected = audit(root)

    if inspected == 0:
        # No workspace declares a lint policy. That is a real state, not a
        # silent pass: say which it is.
        ok("[lints_optin] no workspace declares [workspace.lints] — nothing to inherit")
        return 0
    if findings:
        err(f"[lints_optin] {len(findings)} crate(s) not subject to the workspace lint policy:")
        for f in findings:
            err(f"    {f}")
        info("  `[workspace.lints]` is a declaration; a crate applies it with:")
        info("      [lints]")
        info("      workspace = true")
        info("  Without that line the crate has no findings because it is subject")
        info("  to no lints — which reads exactly like a clean crate.")
        return 1
    ok(f"[lints_optin] OK — {inspected} workspace member(s) inherit the declared policy")
    return 0


def self_test() -> int:
    """Prove the check can go red, and that it does not fire on a clean tree."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "Cargo.toml").write_text(
            '[workspace]\nmembers = ["a", "b"]\n\n[workspace.lints.clippy]\npedantic = "warn"\n'
        )
        (root / "a").mkdir()
        (root / "a" / "Cargo.toml").write_text('[package]\nname = "a"\n\n[lints]\nworkspace = true\n')
        (root / "b").mkdir()
        (root / "b" / "Cargo.toml").write_text('[package]\nname = "b"\n')

        findings, inspected = audit(root)
        assert inspected == 2, f"expected 2 members, saw {inspected}"
        assert len(findings) == 1, f"expected 1 finding, saw {findings}"
        assert "b/Cargo.toml" in findings[0], findings[0]

        # and clean once b opts in
        (root / "b" / "Cargo.toml").write_text('[package]\nname = "b"\n\n[lints]\nworkspace = true\n')
        findings, inspected = audit(root)
        assert not findings, findings

        # a workspace with no policy is reported as such, not as a pass
        (root / "Cargo.toml").write_text('[workspace]\nmembers = ["a", "b"]\n')
        findings, inspected = audit(root)
        assert inspected == 0 and not findings

    ok("[lints_optin] self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
