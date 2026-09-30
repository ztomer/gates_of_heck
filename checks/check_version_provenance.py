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
a git hash and a build date, and that something sets them. That is cheap,
portable, and it fails for the reason we care about -- a version string written
without provenance.

## Scope, stated rather than implied

Only a version string the check can SEE is covered. A project that computes its
version at runtime from a file, or from an env var supplied by a packaging
system, is not matched by this and should not pretend otherwise -- pass
`--allowlist` or exclude it explicitly rather than leaving it silently
unchecked.

Exit codes: 0 clean, 1 findings, 2 a usage or environment error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Anything that reads as a commit-ish provenance source in a version string.
HASH_ENV = re.compile(
    r"env!\(\s*\"[A-Z0-9_]*(GIT_HASH|GIT_SHA|COMMIT_SHA|COMMIT|BUILD_HASH|VCS_HASH|SOURCE_HASH)[A-Z0-9_]*\"\s*\)",
    re.IGNORECASE,
)
DATE_ENV = re.compile(
    r"env!\(\s*\"[A-Z0-9_]*(BUILD_DATE|BUILD_TIME|BUILD_TIMESTAMP|BUILT_AT|SOURCE_DATE)[A-Z0-9_]*\"\s*\)",
    re.IGNORECASE,
)
# A version is declared INSIDE a `#[command(...)]` attribute, in either clap
# spelling: the bare shorthand `version,` (by far the common one) or
# `long_version = "..."`. The first version of this regex required an `=`, so
# it matched neither real spelling and reported a compliant clap binary as
# clean -- found by calibrating against a real fixture rather than a sketch.
COMMAND_ATTR = re.compile(r"#\s*\[\s*command\s*\((?P<body>.*?)\)\s*\]", re.DOTALL)
BARE_VERSION = re.compile(r"(?:^|,)\s*version\s*(?:,|$)")
LONG_VERSION = re.compile(r"\blong_version\s*=|\bversion\s*=\s*\"")

BINDIR = Path("src/bin")
MAIN_FILES = ("src/main.rs",)


def rust_package_roots(root: Path) -> list[Path]:
    """Every package directory in the tree, workspace members included.

    The first version looked only at `<root>/src/main.rs`, so it found
    nothing in either real repository -- both are WORKSPACES, with binaries
    under `crates/*/src/main.rs`. A gate that never reaches the code is a gate
    that reports compliance over zero files.
    """
    seen: set[Path] = set()
    for crate in root.rglob("Cargo.toml"):
        # Skip vendored and target directories: their sources are not ours to
        # hold to this repo's policy, and they are large.
        parts = set(crate.relative_to(root).parts)
        if parts & {"target", ".build", "vendor", "node_modules"}:
            continue
        seen.add(crate.parent)
    if not seen:
        seen.add(root)
    return sorted(seen)


def rust_binary_roots(root: Path) -> list[Path]:
    """Every file that could carry a binary's version string."""
    out: list[Path] = []
    for pkg in rust_package_roots(root):
        manifest = pkg / "Cargo.toml"
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="replace")
            is_lib_only = 'crate-type = ["lib"]' in text or (
                "[[bin]]" not in text and (pkg / "src" / "lib.rs").is_file()
            )
            if is_lib_only:
                continue
        out.extend(pkg / p for p in MAIN_FILES if (pkg / p).is_file())
        bindir = pkg / "src" / "bin"
        if bindir.is_dir():
            out.extend(sorted(bindir.glob("*.rs")))
    return [p for p in out if p.is_file()]


def sets_provenance(pkg: Path) -> tuple[bool, bool]:
    """Does a build script set a commit hash and a build date?"""
    for name in ("build.rs", "build/build.rs"):
        script = pkg / name
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8", errors="replace")
        has_hash = bool(re.search(r"rustc-env=[A-Z0-9_]*(GIT_HASH|GIT_SHA|COMMIT|BUILD_HASH|VCS_HASH|SOURCE_HASH)", text, re.I))
        has_date = bool(re.search(r"rustc-env=[A-Z0-9_]*(BUILD_DATE|BUILD_TIME|BUILD_TIMESTAMP|BUILT_AT|SOURCE_DATE)", text, re.I))
        if has_hash or has_date:
            return has_hash, has_date
    return False, False


def check(root: Path, allowlist: set[str]) -> list[str]:
    problems: list[str] = []
    for path in rust_binary_roots(root):
        rel = str(path.relative_to(root))
        if rel in allowlist or str(path) in allowlist:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        declares_version = any(
            BARE_VERSION.search(b) or LONG_VERSION.search(b) for b in COMMAND_ATTR.findall(text)
        )
        if not declares_version:
            continue  # this binary does not declare a version; not our business
        pkg = root
        for cand in rust_package_roots(root):
            if path.is_relative_to(cand):
                pkg = cand
                break
        hash_ok, date_ok = sets_provenance(pkg)
        if not HASH_ENV.search(text):
            problems.append(
                f"{rel}: declares a version but never embeds a commit hash "
                f"(expected an env!(\"...GIT_HASH\")-style field)"
            )
        if not DATE_ENV.search(text):
            problems.append(
                f"{rel}: declares a version but never embeds a build date "
                f"(expected an env!(\"...BUILD_DATE\")-style field)"
            )
        if not (hash_ok and date_ok):
            missing = [
                n for n, ok in (("commit hash", hash_ok), ("build date", date_ok)) if not ok
            ]
            problems.append(
                f"{rel}: the version string references provenance but no build "
                f"script sets {' and '.join(missing)} (expected a build.rs "
                f"emitting cargo:rustc-env)"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", nargs="?", default=".", help="repository root (default: .)")
    ap.add_argument(
        "--baseline",
        default=None,
        help="JSON file of paths known not to comply; entries are skipped and may shrink",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

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

    problems = check(root, allowlist)
    if args.json:
        print(json.dumps({"findings": problems}, indent=2))
        return 1 if problems else 0
    for p in problems:
        print(f"✗ [version_provenance] {p}")
    if problems:
        print(f"--- {len(problems)} finding(s) ---")
        return 1
    print("✓ [version_provenance] every declared version carries a commit and a build date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
