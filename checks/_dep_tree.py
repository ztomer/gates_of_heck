"""Reading a Cargo tree: manifests, lockfiles, and what each one declares.

Split out of `check_dep_currency.py` for the file-length cap, and because
reading a tree is a distinct concern from deciding what a finding IS. This half
touches only the filesystem and TOML; it has no notion of severity, of crates.io
or of what the run will report. The checker imports it, the other way round never
happens.

Everything a malformed tree can do is contained here, which is what keeps the
checker from having to defend itself against it:

* a manifest that does not parse is SKIPPED, not fatal. One repository's typo
  must not take down the check for every other repository in the tree.
* `[workspace.dependencies]` is read from the WORKSPACE ROOT. A member saying
  `toml = { workspace = true }` inherits from the root's table and has no
  `[workspace]` section of its own, so reading it from the member yields nothing
  -- which reads as "no version declared" and silently exempts every inherited
  dependency from the check.
* `path` and `git` requirements have no crates.io version to be behind, so they
  are dropped rather than reported as an empty requirement.
* the lockfile that governs a manifest is its own, else the nearest above.

A `Dep` therefore carries both the requirement as written and WHERE it was
written, because a finding quotes the section: the same crate in
`[dependencies]` and in `[target.'cfg(unix)'.dependencies]` are two declarations.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

# Dependency tables that ship. `[target.'cfg(...)'.dependencies]` is handled
# separately because it nests one more level.
DEP_TABLES = ("dependencies", "build-dependencies", "dev-dependencies")


@dataclass
class Dep:
    """One declared dependency, with where it was declared and what it asked for."""

    name: str
    req: str
    manifest: Path
    section: str
    inherited: bool = False


# ── reading a tree ────────────────────────────────────────────────────────────


def manifests(root: Path) -> list[Path]:
    """Every Cargo.toml in the tree, skipping vendored and target trees."""
    from _gitutil import cargo_manifests

    return cargo_manifests(root, {"target", "vendor", ".git", "node_modules", "build", ".build"})


def read_manifest(path: Path) -> dict | None:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def workspace_dependencies(manifest: Path, doc: dict) -> dict:
    """`[workspace.dependencies]`, from the WORKSPACE ROOT.

    A member saying `toml = { workspace = true }` inherits from the ROOT's
    table, and a member manifest has no `[workspace]` section of its own, so
    reading it from the member yields nothing -- which reads as "no version
    declared" and silently exempts every inherited dependency from the check.
    """
    if isinstance(doc.get("workspace"), dict) and isinstance(
        doc["workspace"].get("dependencies"), dict
    ):
        return doc["workspace"]["dependencies"]
    for parent in manifest.parents:
        cand = parent / "Cargo.toml"
        if not cand.is_file():
            continue
        other = read_manifest(cand)
        if other and isinstance(other.get("workspace"), dict):
            deps = other["workspace"].get("dependencies")
            if isinstance(deps, dict):
                return deps
    return {}


def declared_deps(doc: dict, path: Path) -> list[Dep]:
    """Direct dependencies, with workspace inheritance resolved."""
    ws_deps = workspace_dependencies(path, doc)
    out: list[Dep] = []

    def take(table: dict, section: str) -> None:
        for name, spec in table.items():
            # Bound before the branches, not in one of them: a dict-valued
            # requirement reached first crashed the whole run on media_server,
            # and two repos had never exercised that path.
            inherited = False
            if isinstance(spec, str):
                req = spec
            elif isinstance(spec, dict):
                if "path" in spec or "git" in spec:
                    continue  # no crates.io version to be behind
                if spec.get("workspace") is True:
                    ws_spec = ws_deps.get(name)
                    if not isinstance(ws_spec, (str, dict)):
                        continue  # cannot resolve; abstain quietly
                    req = ws_spec if isinstance(ws_spec, str) else ws_spec.get("version", "")
                    inherited = True
                else:
                    req = spec.get("version", "")
                    if not req:
                        continue
            else:
                continue
            out.append(Dep(name, req, path, section, inherited))

    for table in DEP_TABLES:
        if isinstance(doc.get(table), dict):
            take(doc[table], table)
    tgt = doc.get("target")
    if isinstance(tgt, dict):
        for plat, block in tgt.items():
            if isinstance(block, dict):
                for table in DEP_TABLES:
                    if isinstance(block.get(table), dict):
                        take(block[table], f"target.{plat}.{table}")
    return out


def lock_versions(lock: Path) -> dict[str, list[str]]:
    """Crate name -> every version the lockfile pins for it."""
    try:
        with lock.open("rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out: dict[str, list[str]] = {}
    for pkg in doc.get("package", []) or []:
        name, version = pkg.get("name"), pkg.get("version")
        if isinstance(name, str) and isinstance(version, str):
            out.setdefault(name, []).append(version)
    return out


def nearest_lock(manifest: Path) -> Path | None:
    """The lockfile that governs this manifest: its own, else the nearest above."""
    for parent in [manifest.parent, *manifest.parents]:
        cand = parent / "Cargo.lock"
        if cand.is_file():
            return cand
    return None
