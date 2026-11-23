"""The goh binary's stamp names every file the build embeds (GOH.STAMPINPUTS).

The stamp (lib/goh_version.sh goh_stamp_at) was the git trees of a hand list of four roots, and the
binary `include_str!`s lib/busyloop_shapes.json and gates/required_tools.tsv from outside them: an
edit to either alone left the stamp, so the store served the stale build and the gates judged with
the old busy-loop shapes or tool manifest. The inputs are now DERIVED (goh_stamp_inputs_at) from the
crates' own include_str!/include_bytes!/include! literals. These hold the derivation to the tree:
an independent scan of HEAD's crates finds no embedded file the stamp misses, the test modules the
derivation skips by file name compile only under cfg(test), and a build script reads no file but by
`include_str!` (where the derivation sees it).
"""

from __future__ import annotations

import posixpath
import re
import subprocess

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "lib" / "goh_version.sh"
# Its whole read of the tree (lib/declared_reads.py JUDGE_READS): HEAD's crates, the stamp script,
# and the tracked names an embed in them targets (a new target is a crates/ change).
JUDGE_READS = ("crates/", "lib/goh_version.sh")
MACRO = re.compile(r'\binclude(?:_str|_bytes)?!\s*\(\s*"([^"]*)"')
MODDECL = re.compile(
    r"^((?:[ \t]*#\[[^\]]*\]\s*)*)[ \t]*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.MULTILINE
)
PATHATTR = re.compile(r'#\[path\s*=\s*"([^"]*)"\]')
FS_READ = re.compile(r"\b(?:fs::read(?:_to_string)?|File::open|read_to_string)\s*\(")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                          check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip


def _sources() -> dict[str, str]:
    """HEAD's crates/**.rs, path -> text (HEAD's: what the stamp is derived from)."""
    names = [n for n in _git("ls-tree", "-r", "--name-only", "HEAD", "crates").split("\n")
             if n.endswith(".rs")]  # fmt: skip
    return {n: _git("show", f"HEAD:{n}") for n in names}


def _test_named(path: str) -> bool:
    base = posixpath.basename(path)
    return base == "tests.rs" or base.endswith("_tests.rs")


def _integration(path: str) -> bool:
    return "/tests/" in path


def test_every_embedded_file_is_a_stamp_input() -> None:
    sources = _sources()
    tracked = set(_git("ls-tree", "-r", "--name-only", "HEAD").split("\n"))
    want = set()
    for path, text in sources.items():
        if _test_named(path) or _integration(path):
            continue
        for lit in MACRO.findall(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(path), lit))
            if not target.startswith("crates/") and target in tracked:
                want.add(target)
    assert want, "the scan found no embedded file: the instrument is blind (the binary embeds some)"
    got = subprocess.run(["bash", str(SCRIPT), "--inputs", str(REPO_ROOT), "HEAD"],
                         capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL).stdout.split()  # fmt: skip
    assert got[:4] == ["crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml"]
    assert want - set(got) == set(), "embedded but not stamped: an edit to it keeps a stale build"


def _declared(path: str, decl: str, attrs: str, sources: dict[str, str]) -> str | None:
    """The file `mod decl;` declared in `path` loads, if it is one of the sources."""
    here = posixpath.dirname(path)
    given = PATHATTR.search(attrs)
    if given:
        return posixpath.normpath(posixpath.join(here, given.group(1)))
    base = posixpath.basename(path)
    if base not in ("mod.rs", "lib.rs", "main.rs", "build.rs"):
        here = posixpath.join(here, base[: -len(".rs")])
    for cand in (posixpath.join(here, f"{decl}.rs"), posixpath.join(here, decl, "mod.rs")):
        if cand in sources:
            return cand
    return None


def test_every_test_named_module_compiles_only_under_cfg_test() -> None:
    """The derivation skips `tests.rs` / `*_tests.rs` by NAME; a production module so named would
    hide its embeds from the stamp. Each such file is declared, and only ever under cfg(test)."""
    sources = _sources()
    gated: dict[str, list[bool]] = {}
    for path, text in sources.items():
        for m in MODDECL.finditer(text):
            target = _declared(path, m.group(2), m.group(1), sources)
            if target is not None:
                gated.setdefault(target, []).append("#[cfg(test)]" in m.group(1))
    named = [p for p in sources if _test_named(p) and not _integration(p)]
    assert named, "no test-named module found: the instrument is blind"
    wrong = sorted(p for p in named if not gated.get(p) or not all(gated[p]))
    assert wrong == [], "a test-named module outside cfg(test): its embeds would skip the stamp"


def test_no_build_script_reads_a_file_but_by_include() -> None:
    """A build script's file read at run time is a build input the derivation cannot see; an
    `include_str!` it can (crates/goh-tui/build.rs embeds tui/stylerc)."""
    scripts = {p: t for p, t in _sources().items() if posixpath.basename(p) == "build.rs"}
    assert scripts, "no build script found: the instrument is blind"
    reads = sorted(p for p, t in scripts.items() if FS_READ.search(t))
    assert reads == [], "a build script reads a file at run time: embed it with include_str!"
