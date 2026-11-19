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

Any other variable is a HOLE (`place` is None): the proven closure has no edge to follow, and the
coverage key declares the form with why it is keyed or no input, or refuses (LookupError).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import source_closure  # lib/, beside this file
from source_closure import ROOT_VARS, SELF_VARS

SCRIPT = (".sh", ".py", ".bash")
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
    out: dict[str, Path | None] = {}
    for raw in text.splitlines():
        for m in ROOTED.finditer(source_closure._strip_comment(raw)):
            var, tail = m.groups()
            if not tail.endswith(SCRIPT):
                continue
            form = f"${var}{tail}" if var else f"$(dirname $0){tail}"
            out.setdefault(form, place(var, tail, here, goh, vars_))
    return list(out.items())


def run_closure(seeds: set[str], root: Path, goh: Path, named_edges: bool = False) -> set[str]:
    """seeds and every file under root they source, import (source_closure.closure) or run through
    a declared root; with `named_edges`, every file their code names by path too."""
    files = set(seeds)
    while True:
        found, _ = source_closure.closure(sorted(files), root, goh, named_edges)
        grown = set(found)
        for rel in found:
            if not source_closure._is_shell(root / rel):
                continue
            for _, target in script_paths(root / rel, goh):
                if target is not None and target.is_file() and target.is_relative_to(root):
                    grown.add(target.relative_to(root).as_posix())
        if grown <= files:
            return files
        files |= grown
