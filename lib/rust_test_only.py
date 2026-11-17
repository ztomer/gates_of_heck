"""Which Rust sources of a gates root no release build compiles (GOH.PROOFKEY).

lib/gate_identity.py keys the goh binary the gates run by its build inputs' content. A change to a
test module alone was a new key in every consumer's proven step, though the release binary is the
same program. These are left out -- PROVEN from the declaring source, never guessed from a name.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

# `#[cfg(test)]`, any further attributes (`#[path = "x_tests.rs"]`), then an out-of-line `mod x;`.
_TEST_MOD = re.compile(
    r"^[ \t]*#\[cfg\(test\)\][ \t]*\n((?:[ \t]*(?:#\[[^\n]*\]|//[^\n]*)?[ \t]*\n)*)"
    r"[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?mod[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*;",
    re.MULTILINE,
)
_PATH_ATTR = re.compile(r'#\[path[ \t]*=[ \t]*"([^"]+)"\]')
_INCLUDE = re.compile(r'include(?:_str|_bytes)?!\s*\((?:[^;"]*?,)?\s*"([^"]+\.rs)"')


def find(root: Path, rels: list[str], embedded: Callable[[], list[str]]) -> frozenset[str]:
    """The Rust sources under root's crates/ that no release build compiles (GOH.PROOFKEY): every
    file of a module declared `#[cfg(test)] mod x;` (its file, and its directory's tree), and each
    crate's integration tests/ -- PROVEN from the declaring source, never from a file's name: a
    `tests.rs` whose `mod tests;` lacks the cfg is built, and keyed. A file a crate embeds
    (build_inputs: `include_str!` and friends) stays keyed whatever its place. A change to these
    alone is no new verdict: the binary the gates run is the same program."""
    out: set[str] = set()
    texts: dict[str, str] = {}
    for rel in rels:
        parts = rel.split("/")
        if len(parts) > 3 and parts[2] == "tests":  # crates/<crate>/tests/**: integration tests
            out.add(rel)
            continue
        try:
            text = texts[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        here = Path(rel).parent
        stem = Path(rel).stem
        # A child of foo.rs lives in foo/; of mod.rs, lib.rs or main.rs, beside it.
        base = here if stem in ("mod", "lib", "main") else here / stem
        for m in _TEST_MOD.finditer(text):
            attrs, name = m.group(1), m.group(2)
            path = _PATH_ATTR.search(attrs)
            if path:
                target = Path(os.path.normpath(here / path.group(1)))
                tree = target.parent if target.name == "mod.rs" else target.with_suffix("")
                files = [target.as_posix()]
            else:
                files = [(base / f"{name}.rs").as_posix(), (base / name / "mod.rs").as_posix()]
                tree = base / name
            out.update(f for f in files if f in rels)
            prefix = tree.as_posix() + "/"
            out.update(r for r in rels if r.startswith(prefix))
    if out:
        out -= set(embedded())
        # A built source that includes one (`include!("tests.rs")`) makes it built: keyed. By the
        # included name's last component, so a path spelled through concat!/env! still counts.
        for rel, text in texts.items():
            if rel in out:
                continue
            for m in _INCLUDE.finditer(text):
                name = m.group(1).rsplit("/", 1)[-1]
                out -= {f for f in out if f.rsplit("/", 1)[-1] == name}
    return frozenset(out)
