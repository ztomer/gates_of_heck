"""The Rust coverage measurement's scripts: gates/rust_gate.sh's closure (GOH.COVKEYGAP).

THE MISS. The measurement's `scripts` line (lib/gate_identity.py `rust`) hashed gates/rust_gate.sh,
gates/_rust_proven.sh and the closure of gates/coverage_gate.sh, a hand-chosen pair of seeds whose
own sources were never followed: 13 files rust_gate.sh and _rust_proven.sh SOURCE were outside it
(gates/_proven.sh, gates/_hash.sh, gates/_common.sh -- whose goh_step judges the coverage step
through lib/xexit.py -- and the rest), and 36 with what they run. An edit to any of them reused a
coverage verdict another program had measured.

THE CLOSURE NOW. Every file gates/rust_gate.sh reaches, transitively: what it sources and imports
(lib/source_closure.py), and each script a shell script's code names by a rooted path
(`"$GOH_PY" "$PROVEN_GOH_ROOT/lib/gate_identity.py"`, `bash "$HERE/x.sh"`), placed through a
DECLARED root (lib/run_rule.py, GOH.RUNRULEROOTS): a script through any other variable is a hole,
and so is one through a declared root to no file. A data path (`$HERE/../lib/x.json`) is an edge
when its tail, read from the script's directory or the gates root, names a file under a code part
of goh (gates/ checks/ lib/ tools/ tui/ scripts/); a name at the root (`$cargo_dir/Cargo.toml`,
`$ROOT/.gatesrc`) is the gated repo's own. Following a name is the over-approximation a key wants
(a wrong reuse is worse than a slow gate).

A HOLE fails loudly. A `source`/`.` whose word the reading cannot place (`. "$_goh_tui"`), a run
of a bare variable (`exec "$goh_native"`, `bash -c "$EXTERNAL"`) and a script through an undeclared
root (`$GOH_GIT_ROOT/tui/lib.sh`) could reach any file. Each one in
the closure is DECLARED below with what it reaches and why that is keyed or no input; an undeclared
one raises LookupError, so the measurement has no identity and runs (gate_identity.py `rust` exits
1), and tests/test_cov_key_closure.py fails on it. A Python file of the closure that runs a script
by path is RUNS_BY_NAME; the test reads every script a closure .py names and holds the mentions.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import run_rule  # lib/, beside this file: a script path's declared root (GOH.RUNRULEROOTS)
import source_closure  # lib/, beside this file

RUST_GATE = ("gates/rust_gate.sh",)  # the entry: _rust_proven.sh and coverage_gate.sh are its edges
RUST_MEASURE = "gates/coverage_gate.sh"
CODE_PARTS = run_rule.CODE_PARTS
# A run of a bare variable: the command it names is decided at run time.
DYNAMIC_RUN = re.compile(
    r'(?:(?<![\w/-])(?:bash|sh|exec|python3?)|"\$\{?GOH_PY\}?")\s+(?:-[A-Za-z-]+\s+)*'
    r'"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"?(?=[\s;|&)]|$)'
)

_GOH_BIN = "the goh binary: the build of the gates root's crates/, never run by the measurement"
_HELPER = "a helper the same file assigns by a rooted path, an edge of the closure already"
# `<file>: <the form>` -> why what it reaches is keyed, or is no input of the measurement.
DECLARED = {
    'gates/_rust_coverage.sh: bash -c "$GOH_COV_RUST_EXTERNAL"': "the external suite: the "
    "`runner` line",
    'gates/_coverage_rust.sh: bash -c "$EXTERNAL"': "the external suite: the `runner` line",
    'gates/local_ci.sh: bash -c -- "$cmd"': "a step of the repo's local CI (gates/local_ci.sh)",
    'gates/_export_bin_goh.sh: exec "$goh_native"': _GOH_BIN,
    'gates/goh.sh: exec "$goh_native"': _GOH_BIN,
    'gates/local_ci.sh: exec "$GOH_RESOLVED_BIN"': _GOH_BIN,
    'gates/local_ci.sh: exec "$GOH_PY"': "the house interpreter, `tool python3`",
    'gates/_proven_identity.sh: python3 "$GOH_PY"': "the house interpreter, `tool python3`",
    'gates/_from_head.sh: exec "$BASH"': "this bash, re-running HEAD's copy of the script",
    'lib/bash5.sh: exec "$cand"': "a bash 5 found on PATH to re-run the same script under",
    "lib/bash5.sh: bash ${BASH_VERSION}": "a refusal's message naming the running bash, no run",
    'gates/_py.sh: python -c "$code"': "a version probe whose code is the file's own literal",
    'gates/_proven_closure.sh: "$GOH_PY" -S "$_PROVEN_CLOSURE_PY"': _HELPER,
    'gates/_proven_identity.sh: "$GOH_PY" -S "$_PROVEN_IDENTITY_PY"': _HELPER,
    'gates/_proven_why.sh: "$GOH_PY" -S "$_PROVEN_IDENTITY_PY"': _HELPER,
    'lib/gate_slot.sh: "$GOH_PY" -S "$helper"': _HELPER,
    'lib/gate_slot.sh: "$GOH_PY" -S "$LAND_SHARE_HELPER"': _HELPER,
    'lib/worker_share.sh: "$GOH_PY" -S "$WORKER_SHARE_HELPER"': _HELPER,
    # A script path or source word no reading places: lib/run_rule.py's table, the step closure's.
    **run_rule.DECLARED,
}
# A Python file of the closure that runs another by path: `rel` -> the files, kept as edges.
RUNS_BY_NAME = {
    "gates/required_judge.py": ("gates/python.sh",),  # `bash HERE/python.sh -V`
    "lib/source_closure.py": ("checks/_prose_mask.py",),  # spec_from_file_location
    "checks/_goh_tmp.py": ("lib/tmp_root.py",),  # spec_from_file_location
}


def runs(rel: str, root: Path, goh: Path) -> tuple[list[Path], list[str]]:
    """The files a shell script's code names by a rooted path, and its holes, as `<rel>: <form>`:
    each script path (lib/run_rule.py) through no declared root, or through one to no file; each
    source word nothing places; each run of a bare variable. A DECLARED script form, and a data
    path, is read as a goh code file from the script's directory or the gates root: following a
    name over-approximates, which a key wants."""
    here = Path(rel).parent
    edges: list[Path] = []
    holes: list[str] = []
    for form, target in run_rule.script_paths(root / rel, goh, root):
        if target is not None and target.is_file():
            edges.append(target)
        elif target is not None:
            holes.append(f"{rel}: {form} names no file")
        else:  # a hole, met declared or not; a declared one is followed by its tail too
            holes.append(f"{rel}: {form}")
            edges += _guess(form[form.index("/") :], here, root)
    for raw in (root / rel).read_text(encoding="utf-8", errors="replace").splitlines():
        line = source_closure._strip_comment(raw)
        for m in run_rule.ROOTED.finditer(line):
            if not m.group(2).endswith(run_rule.SCRIPT):  # a script path is read above
                edges += _guess(m.group(2), here, root)
        holes += [f"{rel}: {m.group(0).strip()}" for m in DYNAMIC_RUN.finditer(line)]
    return edges, holes + run_rule.source_holes(rel, root)


_guess = run_rule.guess  # the goh code file a rooted path's tail names


def closure(
    seeds, root: Path, goh: Path, outside: set[str] | None = None
) -> tuple[set[str], list[str]]:
    """seeds and every file under root they source, import or run (RUNS_BY_NAME too), and every
    hole met (`<rel>: <form>`), declared or not. One read of each file. A file of goh when goh is
    not root (a consumer's runner sourcing `$GOH_DIR/lib/x`) lands in `outside`, goh-relative."""
    root, goh = Path(os.path.normpath(root)), Path(os.path.normpath(goh))
    files: set[str] = set()
    holes: set[str] = set()
    todo = sorted(seeds)
    while todo:
        rel = todo.pop()
        path = root / rel
        if rel in files or not path.is_file():
            continue
        files.add(rel)
        found = list(RUNS_BY_NAME.get(rel, ()))
        deps = source_closure.direct(path, root, goh)[0]
        if source_closure._is_shell(path):
            edges, met = runs(rel, root, goh)
            deps += edges
            holes.update(met)
        for raw in deps:
            dep = Path(os.path.normpath(raw))
            if dep.is_relative_to(root):
                found.append(dep.relative_to(root).as_posix())
            elif outside is not None and dep.is_relative_to(goh):
                outside.add(dep.relative_to(goh).as_posix())
            # else outside both trees: a system file
        todo += [f for f in found if f not in files]
    return files, sorted(holes)


def env_readers(root: Path, files: list[str]) -> list[str]:
    """The files of `files` whose code can read a variable (lib/cov_env.py scans their names): a
    data file a script reads (gates/verdict_free_keys.txt, tui/stylerc) reads none, and its prose
    (`GOH_SHIP_*`) would key a whole family the measurement never reads."""
    return [f for f in files if f.endswith((".rs", ".py")) or source_closure._is_shell(root / f)]


def undeclared(holes: list[str]) -> list[str]:
    return [h for h in holes if h not in DECLARED]


def rust_gate_scripts(root: Path) -> list[str]:
    """The Rust coverage measurement's scripts under root: gates/rust_gate.sh's closure. Raises
    LookupError on a hole no DECLARED entry places: no key can follow it."""
    files, holes = closure(set(RUST_GATE), root, root)
    if missing := undeclared(holes):
        raise LookupError(
            "the coverage scripts reach what no reading places: " + "; ".join(missing)
        )
    return sorted(files)


def _runner_seeds(repo: Path, external: str) -> set[str]:
    """Each word of GOH_COV_RUST_EXTERNAL that names a file of the repo (`./tools/pytest.sh`)."""
    import shlex

    try:
        words = shlex.split(external, comments=True)
    except ValueError:
        words = external.split()
    seeds = set()
    top = repo.resolve()
    for w in words:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):  # VAR=value: an assignment, not a file
            continue
        cand = Path(os.path.normpath(top / w))
        try:
            rel = cand.relative_to(top).as_posix()
        except ValueError:
            continue
        if cand.is_file():
            seeds.add(rel)
    return seeds


def runner_files(repo: Path, external: str, goh: Path) -> list[str]:
    """The files the external suite's command runs the binary THROUGH (GOH.RUSTCOVKEYSCOPE): each
    word of GOH_COV_RUST_EXTERNAL that names a file of the repo (`./tools/pytest.sh`), and what it
    sources, imports or runs. The suite itself is not one (the owner's rule, GOH.RUSTCOVSKIP):
    anything under tests/ is left out, as is a word naming a directory (`pytest tests/`). A hole
    raises LookupError, as in rust_gate_scripts."""
    if not (seeds := _runner_seeds(repo, external)):
        return []
    files, holes = closure(seeds, repo.resolve(), goh)
    if missing := undeclared(holes):
        raise LookupError(
            "the suite's runner reaches what no reading places: " + "; ".join(missing)
        )
    return sorted(r for r in files if r.split("/")[0] != "tests")


def runner_goh_files(repo: Path, external: str, goh: Path) -> list[str]:
    """The goh files a consumer's runner reaches through goh's root (`. "$GOH_DIR/lib/x.sh"`,
    `bash "$GOH_DIR/gates/y.sh"`) and their closure in goh, goh-relative (GOH.RUNRULEROOTS): they
    decide the run as much as the runner does. Empty for goh's own runner, whose files are the
    repo's. A hole raises LookupError."""
    reach: set[str] = set()
    if seeds := _runner_seeds(repo, external):
        closure(seeds, repo.resolve(), goh, reach)  # its holes are runner_files'
    if not reach:
        return []
    files, holes = closure(reach, goh, goh)
    if missing := undeclared(holes):
        raise LookupError("the suite's runner reaches, in goh, what no reading places: "
                          + "; ".join(missing))  # fmt: skip
    return sorted(files)
