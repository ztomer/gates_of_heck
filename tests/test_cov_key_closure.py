"""The Rust coverage key holds every file gates/rust_gate.sh sources or runs (GOH.COVKEYGAP).

The `scripts` line (lib/gate_identity.py `rust`) hashed a hand-chosen pair of seeds and the closure
of gates/coverage_gate.sh, so 13 files the Rust gate SOURCES (gates/_proven.sh, gates/_hash.sh,
gates/_common.sh, ...) were outside it, and an edit to one reused a measurement another program
made. The closure is derived now (lib/rust_cov_scripts.py); this pins it both ways:

  * an edit to any file the gate's scripts source moves the key;
  * a RATCHET with its own reading, wider than the closure's: every word a closure script runs
    (`bash X`, `exec X`, `"$GOH_PY" X`, `python3 X`) or sources, and every goh script a closure
    Python file names, is in the closure, or is a declared hole, or a declared mention -- and a
    form nothing places (`source "$X"`) fails loudly, at the key and here. Stale entries fail.
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import source_closure as sc
from source_closure import copy_closure

# The closure spans goh's code parts: a change under any may move what these tests read.
TREE_READS = ("gates/", "lib/", "checks/", "tools/", "tui/", "scripts/")
SOURCES = ("gates/rust_gate.sh", "gates/_rust_proven.sh", "gates/coverage_gate.sh")
# A word in the RUN position: after a shell, an interpreter, or exec.
_RUNS = re.compile(
    r'(?:(?<![\w/-])(?:bash|sh|exec|python3?)|"\$\{?GOH_PY\}?")\s+(?:-[A-Za-z-]+\s+)*'
)
# Scripts a closure Python file names in code without running them: why each is no input.
# lib/run_rule.py DECLARED names the file of each hole it declares, a key, no run: read from the
# table itself (`_declares`), never listed here again.
MENTIONS = {
    "gates/required_judge.py -> gates/commit_gate.sh": "a marker it tests for in the gated repo",
    "gates/required_tools.py -> gates/py_gate.sh": "a layer's entry, named in the tool table",
    "gates/required_tools.py -> gates/py_staged.sh": "a layer's entry, named in the tool table",
    "gates/required_tools.py -> gates/swift_gate.sh": "a layer's entry, named in the tool table",
    "gates/required_tools.py -> tools/gate.sh": "the gated repo's tools/gate.sh, read as text",
    "lib/cov_env.py -> tools/pytest.sh": "a WHOLESALE entry's key (a file it scans, keyed)",
    "lib/estate_registry.py -> scripts/estate_registry.py": "a command printed for the owner",
    "lib/gate_coverage.py -> tools/gate.sh": "the gated repo's entry, read as text",
    "lib/reuse.py -> checks/_estate_cache.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> gates/push_gate.sh": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/affected_verdict.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/config_push.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/pre_reuse.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/release_gate.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/ship_full_run.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> lib/ship_verdict.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> tools/affected_tests.py": "a reuse site's name, registry metadata",
    "lib/reuse.py -> tools/landq.py": "a reuse site's name, registry metadata",
    "lib/ship_suite.py -> lib/ship_full_run.py": "a message naming the ship's full run",
    "lib/step_closure.py -> gates/proven.sh": "a proven step key's runner seed, read as source",
    "lib/step_closure.py -> tools/pytest.sh": "a step word's suffix test (`endswith`), no run",
}


def _declares(rel: str, other: str) -> bool:
    """Whether `rel` names `other` only in a lib/run_rule.py DECLARED key: its file, or the tail
    of its form (`$root/tools/gate.sh`, the gated repo's own)."""
    import run_rule

    keys = [k.split(": ", 1) for k in run_rule.DECLARED]
    return rel == "lib/run_rule.py" and any(f == other or g.endswith("/" + other) for f, g in keys)


def _scripts():
    import rust_cov_scripts

    return rust_cov_scripts


def test_an_edit_to_any_file_the_rust_gate_sources_moves_the_coverage_key(tmp_path: Path) -> None:
    """Every file gates/rust_gate.sh, _rust_proven.sh and coverage_gate.sh source is keyed, and an
    edit to gates/_proven.sh, on a copy, moves the `scripts` line's hash."""
    sourced, _ = sc.closure(list(SOURCES), REPO_ROOT, REPO_ROOT)
    keyed = set(gate_identity.rust_gate_scripts(REPO_ROOT))
    assert not sorted(set(sourced) - keyed), "sourced by the Rust gate, not in its coverage key"
    root = tmp_path / "goh"
    copy_closure(root, *keyed, "gates/_proven.sh", root=REPO_ROOT)
    before = gate_identity.content(root, gate_identity.rust_gate_scripts(root))
    with (root / "gates" / "_proven.sh").open("a") as fh:
        fh.write("# an edit\n")
    assert gate_identity.content(root, gate_identity.rust_gate_scripts(root)) != before


def _resolves(word: str, here: Path, root: Path) -> str | None:
    """The tree code file a run or source word names: a rooted path's tail or a literal path, read
    from the script's directory or the root; None for a runtime path or a bare variable."""
    m = re.match(
        r"^(?:\$\{?[A-Za-z_]\w*\}?|\$\(dirname \$\{?BASH_SOURCE(?:\[0\])?\}?\))(/.+)$", word
    )
    tail = m.group(1).lstrip("/") if m else ("" if word.startswith("$") else word)
    for cand in (os.path.normpath(here / tail), os.path.normpath(tail)) if tail else ():
        if "/" in cand and (root / cand).is_file():
            return cand
    return None


def reach_misses(root: Path) -> tuple[list[str], list[str]]:
    """(files a closure script runs, sources or names that the closure misses; holes no DECLARED
    entry places), read independently of the closure's own edges."""
    mod = _scripts()
    files, holes = mod.closure(set(mod.RUST_GATE), root, root)
    parts = tuple(f"{p}/" for p in mod.CODE_PARTS)
    scripts = [f for f in sc.tree_files(root) if f.startswith(parts) and f.endswith((".py", ".sh"))]
    misses = []
    for rel in sorted(files):
        path = root / rel
        if sc._is_shell(path):
            here = Path(rel).parent
            for n, raw in enumerate(path.read_text(errors="replace").splitlines(), 1):
                line = sc._strip_comment(raw)
                starts = [m.end() for m in _RUNS.finditer(line)]
                starts += [m.end() for m in sc._SOURCE.finditer(line)]
                for start in starts:
                    hit = _resolves(sc._word(line, start), here, root)
                    if hit and hit not in files and hit.endswith((".sh", ".py", ".bash")):
                        misses.append(f"{rel}:{n} -> {hit}")
        elif rel.endswith(".py"):
            text = path.read_text(errors="replace")
            code = sc.code_only(rel, text)
            for other in scripts:
                named = other not in files and Path(other).name in text
                mention = f"{rel} -> {other}" in MENTIONS or _declares(rel, other)
                if named and sc.names_path(code, other) and not mention:
                    misses.append(f"{rel} -> {other}")
            if sc.dynamic_imports(ast.parse(text)):
                misses.append(f"{rel}: an import by a name built at run time")
    return misses, mod.undeclared(holes)


def test_no_script_of_the_closure_reaches_a_file_the_closure_misses() -> None:
    misses, holes = reach_misses(REPO_ROOT)
    assert not misses, "reached by the coverage scripts, outside their key:\n" + "\n".join(misses)
    assert not holes, (
        "a form no reading places (declare it in lib/rust_cov_scripts.py DECLARED):\n"
        + ("\n".join(holes))
    )


def test_every_declared_entry_is_still_met() -> None:
    """A ratchet, not a rug: a DECLARED hole, RUNS_BY_NAME edge or MENTIONS entry the tree no longer
    has fails, so the lists only describe what is there."""
    mod = _scripts()
    files, holes = mod.closure(set(mod.RUST_GATE), REPO_ROOT, REPO_ROOT)
    # lib/run_rule.py's shared table is the step closure's too: tests/test_step_closure_refuse.py.
    shared = set(mod.run_rule.DECLARED)
    assert not sorted(set(mod.DECLARED) - set(holes) - shared), "DECLARED entries no script has"
    for rel, reached in mod.RUNS_BY_NAME.items():
        assert rel in files, rel
        text = sc.code_only(rel, (REPO_ROOT / rel).read_text())
        for p in reached:
            assert Path(p).name in text and p in files, f"{rel} no longer runs {p}"
    named = set()
    for entry in MENTIONS:
        rel, other = entry.split(" -> ")
        code = sc.code_only(rel, (REPO_ROOT / rel).read_text())
        if rel in files and other not in files and sc.names_path(code, other):
            named.add(entry)
    assert not sorted(set(MENTIONS) - named), "MENTIONS entries no longer met"


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ('. "$SOMEWHERE"\n', "a source the reading cannot place"),
        ('exec "$SOMETHING" --flag\n', "a run of a bare variable"),
    ],
)
def test_a_form_nothing_places_refuses_the_key(tmp_path: Path, line: str, why: str) -> None:
    """Calibration: the same tree plus one dynamic form in a closure script has NO key (the
    measurement runs), and the ratchet names the form."""
    root = tmp_path / "goh"
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), root=REPO_ROOT)
    gate_identity.rust_gate_scripts(root)  # the copy keys as the tree does
    with (root / "gates" / "_hash.sh").open("a") as fh:
        fh.write(line)
    with pytest.raises(LookupError, match=r"gates/_hash\.sh"):
        gate_identity.rust_gate_scripts(root)
    assert any(h.startswith("gates/_hash.sh: ") for h in reach_misses(root)[1]), why


def test_a_literal_run_the_closure_misses_is_named(tmp_path: Path) -> None:
    """Calibration of the ratchet's own reading: a closure script that runs a goh script by a
    literal path (no variable, so no closure edge) is a miss."""
    root = tmp_path / "goh"
    copy_closure(root, *gate_identity.rust_gate_scripts(REPO_ROOT), "gates/python.sh",
                 "tools/land.sh", root=REPO_ROOT)  # fmt: skip
    with (root / "gates" / "_hash.sh").open("a") as fh:
        fh.write("goh_never_called() { bash tools/land.sh; }\n")
    misses, _ = reach_misses(root)
    assert any(m.endswith("-> tools/land.sh") for m in misses), misses
