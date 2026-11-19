"""The run rule: the file a shell script's `$VAR/x.sh` names, through a DECLARED root or a hole.

THE MISS (GOH.RUNRULEROOTS). The run edge (source_closure.RUN, then here) followed a script a gate
RUNS only through $HERE, $_goh_d, $GOH_ROOT, $GOH_DIR and $GOH. `"$GOH_PY" -S
"$PROVEN_GOH_ROOT/lib/gate_identity.py"` (gates/_rust_proven.sh, _proven.sh, _proven_audit.sh,
_proven_closure.sh) and a run through a variable the script assigns itself (`_goh_py_here`,
`_TUI_DIR`, `PROJECT_ROOT`) were no edge of a proven step's closure. The coverage key
(lib/rust_cov_scripts.py) read the same paths through ANY variable, guessing it named goh, and
dropped one whose guess named no goh code file without a word.

THE RULE. A script path (`.sh`, `.py`, `.bash`) in a shell file's code is PLACED when it is spelled
after

  * the script's own directory (`$(dirname "${BASH_SOURCE[0]}")/`, `${BASH_SOURCE[0]%/*}/`) or a
    SELF_VARS name: read from the script's directory;
  * a variable the script assigns from its own directory, from a placed one, or from a root
    (`source_closure.assigned`): read from what it was assigned;
  * a ROOT_VARS name (the goh checkout: GOH_DIR GOH_ROOT GOH PROVEN_GOH_ROOT): read from goh.

  * a FILE_ROOTS name of that file: read from the script's directory, by declaration.

Any other variable is a HOLE (`place` is None). Both readers refuse one (LookupError: no key, the
step or the measurement runs) unless DECLARED names it with what it reaches; a declared hole is
followed by its tail as a goh code file (`guess`). The coverage key refuses a declared root naming
no file too.

THE STEP CLOSURE TOO (GOH.STEPCLOSUREREFUSE). `run_closure`, every proven gate step's key
(lib/step_closure.py), skipped a hole without a word while the coverage key refused it: on the real
tree gates/_goh_bin.sh's `bash "$here/../scripts/build-goh.sh"`, the builder every step's runner
may run, was in no step's key. `$here` is declared that file's own directory now (FILE_ROOTS), and
the step closure refuses a hole the way the coverage key does, from this one table.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import source_closure  # lib/, beside this file
from source_closure import ROOT_VARS, SELF_VARS

SCRIPT = (".sh", ".py", ".bash")
CODE_PARTS = ("gates", "checks", "lib", "tools", "tui", "scripts")  # goh's code, a tail may name

# A variable ONE file declares as its own directory: `<file>` -> the names. gates/_goh_bin.sh's
# `here="${live:-$me}/gates"` is this root's gates/ (`me`), or, run from an export, the live
# checkout's (lib/goh_root.sh goh_live_root_for): the same goh, keyed here by this root's copy.
FILE_ROOTS = {"gates/_goh_bin.sh": ("here",)}

OWN = "the gated repo's own file (`git rev-parse --show-toplevel`): its tree keys it"
TUI = "tui/lib.sh: the gated repo's own or goh's, which the closure holds (tui/lib.sh)"
# A script path through no declared root that the readers know (`<file>: <form>` -> what it
# reaches and why that is keyed): followed by its tail (`guess`), never refused. The coverage key
# (lib/rust_cov_scripts.py DECLARED) and the step closure (run_closure) read this one table.
DECLARED = {
    "gates/_common.sh: $GOH_GIT_ROOT/tui/lib.sh": TUI,
    "gates/coverage_gate.sh: $GOH_GIT_ROOT/tui/lib.sh": TUI,
    "gates/local_ci.sh: $GOH_GIT_ROOT/tui/lib.sh": TUI,
    "gates/_rust_proven.sh: $d/lib/gate_identity.py": "GOH_DIR's checkout: its own `--root` line",
    # Met by goh's own steps' closures (tests/test_step_closure_refuse.py).
    "gates/_self_gate.sh: $top/gates/commit_gate.sh": OWN,
    "gates/_self_gate.sh: $top/gates/_from_head.sh": OWN,
    "gates/commit_gate.sh: $root/tools/gate.sh": OWN,
    "gates/commit_msg.sh: $root/tools/commit_msg.sh": OWN,
    "gates/push_gate.sh: $root/tools/gate.sh": OWN,
    "gates/doctor.sh: $repo/tools/gate.sh": OWN,
    "gates/push_gate.sh: $worktree/tools/gate.sh": "the pushed commit's export of the gated repo",
    "gates/hook.sh: $fix/gates/hook.sh": "a repaired GOH_DIR's hook, re-entered by a git hook only",
    "install.sh: $target/tools/gate.sh": "the installed repo's gate, written here and never run",
}
# A path after a variable (`${GOH_DIR:-$GOH_ROOT}/x` reads its first name) or the own directory.
ROOTED = re.compile(
    r"(?:\$\{?([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}?"
    r'|\$\(\s*dirname\s+"?\$\{?BASH_SOURCE(?:\[0\])?\}?"?\s*\)'
    r"|\$\{BASH_SOURCE(?:\[0\])?%/\*\})((?:/[A-Za-z0-9_.+-]+)+)"
)
_ROOTS = "|".join(sorted((*SELF_VARS, *ROOT_VARS), key=len, reverse=True))
# The declared roots' runs, for a reader of one line (lib/step_closure.py's consumer reach).
RUN = re.compile(
    rf"\$\{{?({_ROOTS})\}}?/((?:\.\./)?[A-Za-z0-9_./-]+\.(?:sh|py))"
    r'|\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)/([A-Za-z0-9_./-]+\.(?:sh|py))'
)


def place(var: str | None, tail: str, here: Path, goh: Path, vars_: dict[str, Path]) -> Path | None:
    """The absolute path `$var/tail` names (var None: the script's own directory), or None when the
    variable is no declared root."""
    if var in vars_:
        base = vars_[var]
    elif var is None or var in SELF_VARS:
        base = here
    elif var in ROOT_VARS:
        base = goh
    else:
        return None
    return Path(os.path.normpath(base / tail.lstrip("/")))


def script_paths(path: Path, goh: Path) -> list[tuple[str, Path | None]]:
    """(the form as `$VAR/tail`, the file it names or None for a hole) for each distinct script
    path in a shell file's code, comments left out."""
    here = path.parent
    text = path.read_text(encoding="utf-8", errors="replace")
    vars_ = source_closure.assigned(text, here, goh)
    rel = path.relative_to(goh).as_posix() if path.is_relative_to(goh) else ""
    vars_.update(dict.fromkeys(FILE_ROOTS.get(rel, ()), here))
    out: dict[str, Path | None] = {}
    for raw in text.splitlines():
        for m in ROOTED.finditer(source_closure._strip_comment(raw)):
            var, tail = m.groups()
            if not tail.endswith(SCRIPT):
                continue
            form = f"${var}{tail}" if var else f"$(dirname $0){tail}"
            out.setdefault(form, place(var, tail, here, goh, vars_))
    return list(out.items())


def guess(tail: str, here: Path, root: Path) -> list[Path]:
    """The goh code file (CODE_PARTS) a rooted path's tail names, from the script's directory
    (`here`, root-relative) or the root."""
    tail = tail.lstrip("/")
    for cand in (os.path.normpath(here / tail), os.path.normpath(tail)):
        if "/" in cand and cand.split("/", 1)[0] in CODE_PARTS and (root / cand).is_file():
            return [root / cand]
    return []


def run_closure(seeds: set[str], root: Path, goh: Path, named_edges: bool = False) -> set[str]:
    """seeds and every file under root they source, import (source_closure.closure) or run through
    a declared root; with `named_edges`, every file their code names by path too. Raises
    LookupError on a hole DECLARED does not name (GOH.STEPCLOSUREREFUSE): no key can follow it.
    A declared root to no file is no edge here (the closure is read again from the next tree, so
    the file is keyed once it exists); the coverage key refuses one (lib/rust_cov_scripts.py)."""
    files = set(seeds)
    while True:
        found, _ = source_closure.closure(sorted(files), root, goh, named_edges)
        grown, holes = set(found), []
        for rel in found:
            if not source_closure._is_shell(root / rel):
                continue
            for form, target in script_paths(root / rel, goh):
                if target is None and f"{rel}: {form}" in DECLARED:
                    targets = guess(form[form.index("/") :], Path(rel).parent, root)
                elif target is None:
                    holes.append(f"{rel}: {form}")
                    continue
                else:  # one naming no file is no edge: once it exists, it is read in and keyed
                    targets = [target] if target.is_file() else []
                grown |= {t.relative_to(root).as_posix() for t in targets if t.is_relative_to(root)}
        if holes:
            raise LookupError("the closure runs what no reading places: " + "; ".join(holes))
        if grown <= files:
            return files
        files |= grown
