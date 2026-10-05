"""Cargo packages in a tree: where they are, what builds a binary, what the
build script sets. Split out of `check_version_provenance.py` because this is
the half that knows about the FILESYSTEM and the half the checker has no rule
about -- the rules (what a version string must carry, and what to say when it
does not) live in the checker, and a checker's rule cannot be read without also
reading the walk that decides which files the rule is applied to.

The seam is `main_files`, deliberately: the set of files that may DECLARE a
version is the checker's SCOPE, stated by `check_version_provenance.py` and
passed in here, because it is also the line its own calibration narrows
(`tests/test_check_version_provenance.py` patches
`MAIN_FILES = ("src/main.rs", "src/lib.rs")` in the checker to prove the
`src/lib.rs` case is load-bearing). A scope constant owned by the helper would
have put that line in a file no test patches, and the calibration would have
become a no-op that proves nothing.

Every rule below was written by being wrong first, and the comment on each says
what getting it wrong costs. All of them are the same defect wearing different
clothes: a package this walk never reaches is a package nothing is checked in.
"""

from __future__ import annotations

import re
from pathlib import Path

# `cargo:rustc-env=NAME=` / `cargo::rustc-env=NAME=`, both spellings.
RUSTC_ENV = re.compile(r"rustc-env=([A-Z0-9_]+)")

# What a setting build script must be seen DOING before an opaque field counts.
# Source-text heuristics, deliberately: the alternative is running the binary,
# which is the one thing the checker exists not to do. Each is a property with
# several honest spellings, not a formatting rule.
COMMIT_SRC = re.compile(
    r"rev-parse|rev_list|rev-list|write-tree|write_tree|describe|diff-tree|diff_tree", re.I
)
DATE_SRC = re.compile(
    r"SOURCE_DATE_EPOCH|SystemTime|UNIX_EPOCH|civil_from_days|build_date|%Y-%m-%d|%Y%m%d", re.I
)
# Something that moves when the WORKING TREE moves. HEAD does not, which is
# the whole reason this third one exists: without it a build from a dirty tree
# quotes the previous commit and is byte-identical to a clean build of it.
TREE_SRC = re.compile(
    r"write-tree|write_tree|--porcelain|diff-index|diff_index|is_dirty|dirty", re.I
)


def rust_package_roots(root: Path) -> list[Path]:
    """Every package directory in the tree, workspace members included.

    The first version looked only at `<root>/src/main.rs`, so it found
    nothing in either real repository -- both are WORKSPACES, with binaries
    under `crates/*/src/main.rs`. A gate that never reaches the code is a gate
    that reports compliance over zero files.
    """
    from _gitutil import cargo_manifests

    # Vendored and target directories are skipped: their sources are not ours to hold to this
    # repo's policy. The listing never descends them (`_gitutil.tree_files`).
    seen = {m.parent for m in cargo_manifests(root, {"target", ".build", "vendor", "node_modules"})}
    if not seen:
        seen.add(root)
    return sorted(seen)


def rust_binary_roots(root: Path, main_files: tuple[str, ...]) -> list[Path]:
    """Every file that could carry a binary's version string.

    `main_files` is the caller's scope (its `MAIN_FILES`), passed in rather than
    owned here: which files may declare a version is the checker's decision, and
    the checker's own calibration narrows that decision in the checker's source.
    """
    out: list[Path] = []
    for pkg in rust_package_roots(root):
        manifest = pkg / "Cargo.toml"
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="replace")
            # Cargo's own rule for "this package builds a binary": an explicit
            # `[[bin]]`, a `src/main.rs`, or any `src/bin/*.rs`. The original
            # test here was `'crate-type = ["lib"]' or (no [[bin]] and a
            # src/lib.rs)`, which SKIPPED a crate that has both a library and an
            # auto-discovered binary in `src/bin/` -- cargo builds that binary,
            # it has a `--version`, and nothing was reading it. Both spellings
            # of "no binary at all" are now checked explicitly.
            declares_bin = (
                "[[bin]]" in text
                or (pkg / "src" / "main.rs").is_file()
                or any((pkg / "src" / "bin").glob("*.rs"))
            )
            if not declares_bin:
                continue
        out.extend(pkg / p for p in main_files if (pkg / p).is_file())
        bindir = pkg / "src" / "bin"
        if bindir.is_dir():
            out.extend(sorted(bindir.glob("*.rs")))
    return [p for p in out if p.is_file()]


def build_scripts(pkg: Path):
    """[(path, text)] for a package's build scripts, if it has any."""
    out = []
    for name in ("build.rs", "build/build.rs"):
        script = pkg / name
        if script.is_file():
            out.append((script, script.read_text(encoding="utf-8", errors="replace")))
    return out


def sets_named_env(pkg: Path, name: str) -> bool:
    """Does a build script set exactly this env var via cargo:rustc-env?"""
    return any(name in RUSTC_ENV.findall(text) for _, text in build_scripts(pkg))


def clause_is_backed(pkg: Path, name: str) -> list[str]:
    """Why an opaque provenance field is NOT credible, or [] when it is.

    The field's name makes no claim, so the script that sets it has to. All
    three are required, and the third is the one that was silently optional:
    a build script that quotes `rev-parse HEAD` and a date, and never asks
    whether the tree is dirty, produces a version string byte-identical to a
    clean build of the same commit -- which is the exact defect the field
    exists to end.
    """
    scripts = [text for _, text in build_scripts(pkg) if name in RUSTC_ENV.findall(text)]
    if not scripts:
        return [
            f"the version string embeds {name} but no build script sets it "
            f"(expected a build.rs emitting cargo:rustc-env={name}=)"
        ]
    text = "\n".join(scripts)
    missing = [
        label
        for label, pattern in (
            ("a commit", COMMIT_SRC),
            ("a build date", DATE_SRC),
            ("the working tree's state", TREE_SRC),
        )
        if not pattern.search(text)
    ]
    if missing:
        return [
            f"the version string embeds {name} but the build script setting it "
            f"never derives {' and '.join(missing)} -- a provenance field that "
            f"cannot tell a dirty build from a clean one is not provenance"
        ]
    return []
