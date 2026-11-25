"""lib/push_hold.py -- whether asking a consumer's push gate again could answer anything else than
its refusal (lib/push_refusal.py) did (GOH.PINSWEEP).

    tip(repo)                its newest commit that is no pin commit
    closures(steps, repo)    {step: goh's closure for it}
    held(last, repo)         why REPO's push is not retried, or None

WHY. 117 of the 62 sweeps' turns were refusals, 9.0 h; 65 (5.6 h) had the failing steps of the sweep
before, and none of their causes was goh's newest change.

A HOLD. The gate is asked again once the tip moved, or once goh moved what each failing step reads:
one step that reads what it read fails as it did, whatever the others do. A refusal with no
judgement (a commit made while the gate ran, a network) holds nothing. A held repo is named, and
its owner's own push carries the pin.

A STEP'S CLOSURE here is what it reaches in goh, WITHOUT the runner (local_ci.sh, the `goh step`
wrap) that lib/step_closure.py puts in every proven step's key: a key must never miss an input, so
that one moves at every release (measured 2026-11-25: each consumer step tried keyed on goh's whole
content, the runner's closure holding the binary's build inputs), and a hold on it would hold
nothing. A step that names a goh file or runs `goh` reads the binary too; one that names a goh path
no reading places, and one the log does not name, reads all of goh.
Pinned by tests/test_push_hold.py.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import consumer_pins  # lib/, beside this file
import estate_commit
import gate_identity
import push_refusal
import source_closure
import step_closure

GOH = Path(__file__).resolve().parent.parent
UNNAMED = "(a step its log does not name)"
_ROOT_USE = re.compile(rf"\$\{{?(?:{'|'.join(source_closure.ROOT_VARS)})\b")
_ROOT_FILE = re.compile(rf"\$\{{?(?:{'|'.join(source_closure.ROOT_VARS)})\}}?\"?/([\w./-]*\w)")
_ASSIGNS = re.compile(rf"^\s*(?:export\s+)?(?:{'|'.join(source_closure.ROOT_VARS)})=")


def tip(repo: Path) -> str:
    """REPO's newest commit that is no pin commit of goh's ("" with none): what its gate judges
    besides the pins. A hook refresh is no pin: it moves the gate."""
    log = estate_commit.git(repo, "log", "-n", "400", "--format=%H%x09%s", "HEAD").stdout
    for line in log.splitlines():
        sha, _, subject = line.partition("\t")
        if not (consumer_pins.SUBJECT.match(subject)
                and consumer_pins.is_pin_commit(repo, sha, subject)):  # fmt: skip
            return sha
    return ""


def _scripts(seeds: set[str], repo: Path, goh: Path) -> tuple[set[str], bool, bool]:
    """(the goh files the repo's scripts from SEEDS name, whether one runs the goh binary, whether
    each goh path they name is one of goh's files)."""
    top, gohs, seen, todo = repo.resolve(), set(), set(), sorted(seeds)
    binary, placed = False, True
    while todo:
        rel = todo.pop()
        if rel in seen or not (top / rel).is_file():
            continue
        seen.add(rel)
        code = step_closure._code(top, rel)
        binary = binary or bool(step_closure._RUNS_GOH.search(code)
                                or step_closure._DISPATCH.search(code))  # fmt: skip
        found, _ = source_closure.direct(top / rel, top, goh)
        if rel.endswith(".py"):
            placed = placed and not step_closure._PY_GOH.search(code)
        for line in code.splitlines():
            if _ASSIGNS.match(line):
                continue
            named = [goh / m for m in _ROOT_FILE.findall(line)]
            placed = placed and len(named) == len(_ROOT_USE.findall(line))
            placed = placed and all(p.is_file() for p in named)
            found += named
        for dep in (Path(os.path.normpath(p)) for p in found):
            if dep.is_relative_to(goh):
                gohs.add(dep.relative_to(goh).as_posix())
            elif dep.is_relative_to(top):
                todo.append(dep.relative_to(top).as_posix())
    return gohs, binary, placed


def _whole(goh: Path) -> str:
    return f"all {gate_identity.identity(goh)}"


def closure(step: str, repo: Path, goh: Path = GOH) -> str | None:
    """What STEP reaches in GOH, as one line that moves when any of it does; None: unknown."""
    try:
        seeds, mine, why = step_closure._step_seeds(step, repo, goh)
        if step == UNNAMED or (push_refusal.DIED.match(f"✗ {step} failed") and not (seeds or mine)):
            return _whole(goh)  # a gate's own label: no command to read
        more, binary, placed = _scripts(mine, repo, goh)
        if not placed:
            return _whole(goh)
        files, _ = step_closure._goh_reach(seeds | more, goh, judge=False)
    except (LookupError, OSError, SyntaxError, ValueError):
        return None  # a script no reading places (GOH.STEPCLOSUREREFUSE), or that cannot be read
    if seeds or why or binary:
        files = files | set(gate_identity.build_inputs(goh))
    return f"{len(files)} {step_closure._part_hash(goh, tuple(sorted(files)))}"


def closures(steps: tuple[str, ...], repo: Path, goh: Path = GOH) -> dict[str, str | None]:
    return {s: closure(s, repo, goh) for s in steps or (UNNAMED,)}


def held(last: dict | None, repo: Path, goh: Path = GOH) -> str | None:
    """LAST: lib/push_sweep_rows.py verdict(REPO)."""
    if not last or last["status"] != "refused" or not last.get("tip"):
        return None
    was = {s: c for s, c in (last.get("steps") or {}).items() if c}
    if not was or last["tip"] != tip(repo):
        return None
    same = [s for s, c in was.items() if closure(s, repo, goh) == c]
    if not same:
        return None
    return (f"held: not retried, its push gate refused at {'; '.join(last['steps'])} on this same "
            f"tip ({last['tip'][:8]}), and goh moved nothing {'; '.join(same)} reads")  # fmt: skip
