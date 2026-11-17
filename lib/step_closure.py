"""A proven step's goh closure: the goh files that can decide ONE gate step (GOH.PROVENCLOSURE).

    step_closure.py --root GOH... --repo R [--pythonpath PP] [--cache DIR --tree T] --step S...
                    # `full\t<line>`, then `<line>\t<step>` per step

gates/_proven_closure.sh puts each step's line in its proven key; lib/gate_identity.py keeps the
run-constant half (PYTHONPATH's other entries, the tools). Pinned by tests/test_proven_closure.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import ast
import contextlib
import functools
import hashlib
import os
import re
import shlex
from pathlib import Path

import source_closure  # lib/, beside this file
from gate_identity import (
    _RUN,
    PARTS,
    _closure,
    _entries,
    _is_goh_root,
    binary,
    build_inputs,
    identity,
    test_only,
)

# THE PROVEN STEP'S CLOSURE (GOH.PROVENCLOSURE). A proven step was keyed on the content of every
# file under the gates roots' PARTS, so ANY goh ship made every consumer's next push a cold full
# gate. A step is keyed instead on the goh files that can decide it: the RUNNER's (what runs and
# keys every step, and the goh binary it wraps each in) and the STEP's own, read from the step
# string and what it sources, imports, runs and names (lib/source_closure.py, named_edges), across
# the consumer's own scripts into goh. What the reader cannot place keys the step on the whole
# content, as before, and says why: a file that dispatches the goh binary (any goh file may run
# under it), a goh path built at runtime, a module imported by a runtime name, a consumer script
# that runs `goh` or builds a goh path in Python.
RUNNER = ("gates/local_ci.sh", "gates/proven.sh")
_DISPATCH = re.compile(r"\bgoh_native\b|\bGOH_RESOLVED_BIN\b|\$\{?GOH_BIN\b")
_ROOT_VAR = re.compile(r"\$\{?(?:GOH_DIR|GOH_ROOT|GOH|PROVEN_GOH_ROOT)\b")
_DYNAMIC = re.compile(r"\$\{?(?:HERE|_goh_d|GOH_ROOT|GOH_DIR|GOH)\}?/[^\s\"';|&)]*[*$]")
_RUNS_GOH = re.compile(r"(?:^|[\s;&|(`])goh(?=[ \t]+-?[a-z])|[\"']goh[\"']\s*,")
_PY_GOH = re.compile(r"\bGOH_DIR\b|\bGOH_ROOT\b|gates_of_heck")
_STEP_VAR = re.compile(r"\$\{?(GOH_DIR|GOH_ROOT|GOH|PWD)\}?(?=/|$)")
ROOT_PY_DIRS = ("", "lib", "checks", "gates", "tools")


@functools.cache
def candidates(goh: Path) -> list[str]:
    """goh's keyed files (PARTS) a consumer's Python may name by path."""
    return [r for r in source_closure.tree_files(goh) if r.split("/")[0] in PARTS]


@functools.cache
def _code(root: Path, rel: str) -> str:
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return source_closure.code_only(rel, text)


def _step_seeds(step: str, repo: Path, goh: Path) -> tuple[set[str], set[str], str]:
    """(goh files, repo files, why-full) the step string names: a word naming a goh file by
    $GOH_DIR (or the goh path itself), a word naming a file of the repo, the files under a directory
    a pytest step is handed. A step that runs `goh` dispatches the binary: why-full says so."""
    cmd = re.sub(r"^\[[^\]]*\]\s+", "", step.strip())
    try:
        words = shlex.split(cmd, comments=True)
    except ValueError:
        words = cmd.split()
    gohs, mine = set(), set()
    pytest = "pytest" in words or any(w.endswith("pytest.sh") for w in words)
    top = repo.resolve()
    for w in words:
        if w == "goh" or w.endswith("/bin/goh"):
            return set(), set(), "the step runs the goh binary"
        word = _STEP_VAR.sub(lambda m: str(top if m.group(1) == "PWD" else goh), w)
        if "$" in word or not word or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word):
            continue
        cand = Path(os.path.normpath(word if word.startswith("/") else top / word))
        for base, out in ((goh, gohs), (top, mine)):
            try:
                rel = cand.relative_to(base).as_posix()
            except ValueError:
                continue
            if cand.is_file():
                out.add(rel)
            elif cand.is_dir() and pytest and base == top:
                out.update(r.relative_to(top).as_posix() for r in cand.rglob("*.py"))
            break
    return gohs, mine, ""


def _consumer_reach(seeds: set[str], repo: Path, goh: Path) -> tuple[set[str], str]:
    """The goh files the repo's own scripts reach from `seeds`: what they source from $GOH_DIR,
    import from a gates root, or run by $GOH_DIR; why-full when one does what the reader cannot
    place (runs `goh`, builds a goh path at runtime)."""
    top = repo.resolve()
    gohs: set[str] = set()
    seen: set[str] = set()
    todo = sorted(seeds)
    while todo:
        rel = todo.pop()
        if rel in seen or not (top / rel).is_file():
            continue
        seen.add(rel)
        path = top / rel
        code = _code(top, rel)
        if _RUNS_GOH.search(code) or _DISPATCH.search(code):
            return gohs, f"{rel} runs the goh binary"
        found, _ = source_closure.direct(path, top, goh)
        if rel.endswith(".py"):
            found += source_closure.direct(path, goh, goh, ROOT_PY_DIRS)[0]
            if _PY_GOH.search(code):  # a goh file by path: `GOH_DIR / "checks" / "x.py"`
                hits = [c for c in candidates(goh) if Path(c).name in code]
                found += [goh / c for c in hits if source_closure.names_path(code, c)]
        elif source_closure._is_shell(path):
            for line in code.splitlines():
                runs = [
                    m for m in _RUN.finditer(line) if m.group(1) in ("GOH_ROOT", "GOH_DIR", "GOH")
                ]
                for m in runs:
                    found.append(goh / os.path.normpath(m.group(2)))
                if _ROOT_VAR.search(line) and not runs and not source_closure._SOURCE.search(line):
                    return gohs, f"{rel} names a goh path the reader cannot place"
        for raw in found:
            dep = Path(os.path.normpath(raw))
            for base, side in ((goh, "goh"), (top, "repo")):
                try:
                    r = dep.relative_to(base).as_posix()
                except ValueError:
                    continue
                if side == "goh":
                    gohs.add(r)
                else:
                    todo.append(r)
                break
    return gohs, ""


def _goh_reach(seeds: set[str], goh: Path, judge: bool) -> tuple[set[str], str]:
    """seeds' closure in goh (sources, imports, runs, names); with `judge`, why-full when a file of
    it dispatches the goh binary, builds a goh path at runtime or imports by a runtime name."""
    files = _closure(seeds, goh, goh, named_edges=True)
    if not judge:
        return files, ""
    for rel in sorted(files):
        if not (rel.endswith(source_closure.CODE) or source_closure._is_shell(goh / rel)):
            continue  # data: it runs nothing
        code = _code(goh, rel)
        if _DISPATCH.search(code):
            return files, f"{rel} dispatches the goh binary"
        if _DYNAMIC.search(code) and not rel.endswith(".py"):
            return files, f"{rel} builds a goh path at runtime"
        if rel.endswith(".py"):
            try:
                tree = ast.parse((goh / rel).read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            if source_closure.dynamic_imports(tree):
                return files, f"{rel} imports a module by a runtime name"
    return files, ""


@functools.cache
def runner_closure(goh: Path) -> frozenset[str]:
    """What runs and keys every step: RUNNER's closure and the goh binary's build inputs (each step
    runs inside `goh canary`/`goh step`). Its own binary calls are bookkeeping and the wrap, so no
    why-full: a STEP that dispatches the binary is judged on its own reach."""
    files, _ = _goh_reach(set(RUNNER), goh, judge=False)
    return frozenset(files | set(build_inputs(goh)))


def step_closure(step: str, repo: Path, goh: Path) -> tuple[list[str], str]:
    """(the goh files that can decide `step`, root-relative; why the step keys on everything, or
    ""). The runner's closure is in every step's."""
    gohs, mine, why = _step_seeds(step, repo, goh)
    if not why:
        more, why = _consumer_reach(mine, repo, goh)
        gohs |= more
    if not why:
        gohs, why = _goh_reach(gohs, goh, judge=True)
    # The keyed parts only, as `content` keys them: docs/ and tests/ never decided a step.
    keyed = {r for r in gohs | runner_closure(goh) if r.split("/")[0] in PARTS}
    return sorted(keyed), why


def _closure_roots(roots: list[Path], path: str | None, repo: Path) -> list[Path]:
    """Every gates root a step may read goh files from: the named roots, then each on PYTHONPATH."""
    out = list(roots)
    for entry in (path or "").split(":"):
        d = Path(entry).resolve() if entry else None
        if d is not None and d.is_dir() and _is_goh_root(d) and d not in out:
            out.append(d)
    return out


def closures(roots: list[Path], steps: list[str], path: str | None = None,
             repo: Path | None = None, cache: Path | None = None,
             tree: str = "") -> list[str]:  # fmt: skip
    """gates/_proven.sh's per-step goh half (GOH.PROVENCLOSURE): `full <line>` (every gates root's
    whole content, the key of a step the closure cannot place), then `<line>\t<step>` per step:
    sha256 of each gates root's content of the step's closure, and the binary line when the binary
    is not the content's build. A root is read by the closure the FIRST root (GOH_DIR's) gives."""
    where = (repo if repo is not None else Path.cwd()).resolve()
    every = _closure_roots(roots, path, where)
    whole = hashlib.sha256("\n".join(sorted({identity(r) for r in every})).encode()).hexdigest()
    full = f"closure full {whole}"
    out = [f"full\t{full}"]
    for step in steps:
        # A step's line is a function of the repo's tree, the step and every root's whole content:
        # the same three find it again without the walk (3.5 s for one divoom-control step).
        memo = None
        if cache is not None and tree:
            name = hashlib.sha256(f"{tree}\n{step}\n{full}\n{path}".encode()).hexdigest()
            memo = cache / name
            if memo.is_file():
                out.append(f"{memo.read_text(encoding='utf-8').strip()}\t{step}")
                continue
        line = _closure_line(step, every, where, full)
        if memo is not None:
            with contextlib.suppress(OSError):
                memo.parent.mkdir(parents=True, exist_ok=True)
                tmp = memo.with_name(f".{memo.name}.{os.getpid()}")
                tmp.write_text(line + "\n", encoding="utf-8")
                os.replace(tmp, memo)
        out.append(f"{line}\t{step}")
    return out


def _closure_line(step: str, every: list[Path], where: Path, full: str) -> str:
    if not every:
        return full
    files, why = step_closure(step, where, every[0])
    if why:
        return full
    # One line per distinct content: two paths of one export (a land's head export and the
    # checkout on PYTHONPATH) are one input, as `proven` keyed them (GOH.PROVENPYPATH).
    parts = sorted({f"{_part_hash(r, tuple(files))} {binary(r)}" for r in every})
    digest = hashlib.sha256("\n".join(parts).encode()).hexdigest()
    return f"closure {digest} {len(files)}"


@functools.cache
def _hashed(root: Path) -> dict[str, str]:
    """`<kind> <sha256>` per keyed file under root, read once per run: every step's line is a
    selection of these (32 steps of divoom-control re-listed the tree each: 22 s)."""
    return {rel: f"{kind.decode()} {hashlib.sha256(data).hexdigest()}"
            for rel, kind, data in _entries(root, PARTS, test_only(root))}  # fmt: skip


def _part_hash(root: Path, files: tuple[str, ...]) -> str:
    """sha256 of each keyed file under root that `files` names (a directory entry, `crates`,
    names every file under it), by path, kind and bytes."""
    table = _hashed(root)
    tops = tuple(f + "/" for f in files)
    want = set(files)
    h = hashlib.sha256()
    for rel in sorted(table):
        if rel in want or rel.startswith(tops):
            h.update(f"{rel} {table[rel]}\n".encode())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, action="append", default=[])
    ap.add_argument("--repo", type=Path)
    ap.add_argument("--pythonpath")
    ap.add_argument("--step", action="append", default=[])
    ap.add_argument("--cache", type=Path, help="where a step's line is kept by tree and content")
    ap.add_argument("--tree", default="", help="the repo's tree: the cache's key (none: no cache)")
    a = ap.parse_args(argv)
    roots = [r.resolve() for r in a.root]
    print("\n".join(closures(roots, a.step, a.pythonpath, a.repo, a.cache, a.tree)))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
