"""Cargo manifest and lockfile text -> values. Pure: no I/O, no git, no repo.

Split out of `check_lock_version.py` because it is the part that is worth
testing on its own and the part most likely to be re-implemented: "what does a
TOML table say" is a question `check_tag_version.py` already answers for
VERSION declarations, and a third answer is how the three drift apart.

Hand-rolled, like `check_tag_version`'s own TOML reading, and for the same
reason: a hook resolves whatever `python3` is on PATH — often the OS-bundled
3.9, which has no `tomllib` — and a parser that needs a 3.11 feature is a gate
that dies on the machine that needs it most.

Every rule here was written by being wrong first, and the comment on each says
what it costs to get it wrong. That is the reason the module exists at all: the
naive version of each of these reads as nothing, and a member list that reads
as empty is a workspace reporting nothing to compare.
"""
from __future__ import annotations

import re

# Directories whose Cargo.toml files are not this repo's own crates.
SKIP_DIRS = {"target", "vendor", "node_modules", ".build", ".git", ".claude", "build"}

# A `[[package]]` block's fields, in the order cargo writes them.
LOCK_TABLE = re.compile(r"^\[\[package\]\]\s*$")
LOCK_FIELD = re.compile(r"^(\w+)\s*=\s*\"(.*)\"\s*$")
TABLE = re.compile(r"^\s*\[([A-Za-z0-9_.\-]+)\]")
KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")

# `version.workspace = true` -- an INHERITANCE, resolved before any comparison.
INHERIT = re.compile(r"^version\s*\.\s*workspace\s*=\s*true\s*$")
# `version = "1.2.3"`
LITERAL = re.compile(r'^version\s*=\s*"([^"]+)"\s*$')
# MULTILINE, or `^` anchors to the start of the WHOLE section text and a
# `members` key that is not the first line never matches -- a member list read
# as empty, which is a workspace reporting nothing to compare.
#
# `[^\]]*`, not a greedy DOTALL `.*`: greedy runs to the LAST `]` in the
# section, so a manifest whose `members` sits above an `exclude` yields
# `members = [... exclude = ["fuzz"]` and the excluded path becomes a member.
# That is not hypothetical -- it is what `monitor`'s manifest does, and this
# check's first run invented a finding about `fuzz/Cargo.toml`, a directory the
# workspace deliberately excludes.
MEMBERS = re.compile(r"^\s*members\s*=\s*\[([^\]]*)\]", re.DOTALL | re.MULTILINE)


def section(text: str, name: str) -> str:
    """The lines of one top-level TOML table, safe for arrays split over lines.

    Sectioned rather than scanned, because both keys the callers read live
    inside arrays or tables whose boundaries matter: `members` is routinely
    written across ten lines (`divoom-control`'s is), and a per-line regex finds
    an opening bracket and never its partner.
    """
    out: list[str] = []
    inside = False
    for raw in text.splitlines():
        header = TABLE.match(raw)
        if header:
            inside = header.group(1) == name
            if inside:
                continue
        if inside:
            out.append(raw)
    return "\n".join(out)


def parse_lock(text: str) -> dict[str, dict]:
    """{name: {"version": str, "source": str}} for every `[[package]]`.

    A `[[package]]` block ends at the next table of ANY kind, so a trailing
    `[[patch.unused]]` cannot append its name to the last crate: a parser that
    keeps extending the current block invents an entry for a patched registry
    crate, and every later `version` field in the file lands on the wrong entry.
    """
    out: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        if LOCK_TABLE.match(line):
            current = {}
            continue
        if current is None:
            continue
        field = LOCK_FIELD.match(line)
        if field:
            current[field.group(1)] = field.group(2)
            if "name" in current and "version" in current:
                out.setdefault(current["name"], current)
            continue
        if line.startswith("["):
            current = None
    return out


def parse_package(manifest_text: str) -> tuple[str | None, str | None]:
    """(name, version-spec) for the `[package]` table.

    The version spec is a literal (`1.2.3`) or None when it is INHERITED from
    the workspace. None is a third state on purpose: `version.workspace = true`
    resolved as text is the string `true`, and comparing that to `1.36.0`
    invents a finding on every member of a modern workspace.

    Section-aware: the first table after `[package]` ends it. A `version` key
    under `[dependencies]`, `[features]` or `[package.metadata.docs]` belongs to
    something else, and reading it as this crate's release number is how a
    repo's version becomes a transitive dependency's.
    """
    name = version = None
    table = ""
    for raw in manifest_text.splitlines():
        header = TABLE.match(raw)
        if header:
            table = header.group(1)
            if table != "package":
                return name, version
            continue
        if table != "package":
            continue
        key = KEY.match(raw)
        if not key:
            continue
        field, value = key.group(1), key.group(2)
        if field == "name" and name is None:
            name = value.strip('"')
        elif field == "version" and version is None:
            version = None if INHERIT.match(raw) else (LITERAL.match(raw) or [None, None])[1]
    return name, version


def parse_workspace(manifest_text: str) -> tuple[str | None, list[str]]:
    """([workspace.package] version, member globs) from a root manifest."""
    workspace = section(manifest_text, "workspace")
    found = MEMBERS.search(workspace)
    members = re.findall(r'"([^"]+)"', found.group(1)) if found else []
    version = None
    for raw in section(manifest_text, "workspace.package").splitlines():
        hit = LITERAL.match(raw)
        if hit:
            version = hit.group(1)
            break
    return version, members