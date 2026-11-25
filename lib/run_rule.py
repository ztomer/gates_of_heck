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

EVERY KEY MODE, AND SOURCES TOO (GOH.FULLKEYSCRIPTS). scripts/ was a step part of the closure key
alone: the whole-content key (GOH_PROVEN_CLOSURE=off, the default, and `closure full`) read
gate_identity.PARTS, a list of its own, so an edit to scripts/build-goh.sh still reused a step
verdict there. CODE_PARTS is the one list of goh's code now; gate_identity.PARTS is it plus the
binary's build inputs, and every key mode reads that. And the step closure skipped a `source` word
no reading places (`. "$_goh_copy"`, `. "$ROOT/.gatesrc"`) without a word: `source_holes` is the
coverage key's reading of one, and run_closure refuses one DECLARED does not name.

DATA TOO (GOH.STEPDATAKEY). Every key mode still dropped the goh DATA a step reads outside the code
parts: gates/doctor.sh reads `"$GOH_ROOT"/hooks/*`, retired_hooks.sha256 and docs/config/, and
structural.sh's hook currency (lib/hook_currency.py) the first two, so an edit to a stock hook
reused a consumer's doctor or structural verdict. DATA is the one declaration of what goh's code
reads of goh's own tree outside CODE_PARTS (tests/test_step_data_key.py finds every goh-rooted read
and holds this to it, both ways): the whole-content key keys `data_files`, and run_closure adds a
reader's data to its closure.

The run rule (GOH.RUNRULEROOTS): the file a shell script's `$VAR/x.sh` names, placed only through a
DECLARED root -- the script's own directory or `SELF_VARS`, a variable it assigns from its
directory or a root (`source_closure.assigned`), or `ROOT_VARS` (GOH_DIR GOH_ROOT GOH
PROVEN_GOH_ROOT); a `FILE_ROOTS` name, declared as its file's own directory (`gates/_goh_bin.sh`'s
`$here`), the file known by its path in the tree it is read from, GOH_DIR another tree or not
(GOH.FULLKEYSCRIPTS); any other variable is a hole. `run_closure` is the proven step closure's run
edge (re-exported by `gate_identity.py` for `step_closure.py`); it and `rust_cov_scripts.py` both
refuse (LookupError) a hole the shared `DECLARED` table does not name (GOH.STEPCLOSUREREFUSE), a
script path, or a `source` word `source_holes` cannot place (GOH.FULLKEYSCRIPTS); the coverage key
refuses a declared root to no file too. `CODE_PARTS` is goh's code, the parts every proven key mode
reads (`gate_identity.PARTS`). Pinned by `test_run_rule_roots.py`, `test_proven_closure.py`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import source_closure  # lib/, beside this file
from source_closure import ROOT_VARS, SELF_VARS

SCRIPT = (".sh", ".py", ".bash")
CODE_PARTS = ("gates", "checks", "lib", "tools", "tui", "scripts")  # goh's code, a tail may name

# The goh data a goh file reads outside CODE_PARTS: `<reader>` -> paths under the goh checkout,
# `dir/*` each file directly in dir (hooks/claude/ is no stock hook). The ONE list: gate_identity's
# whole content keys every file it names, a step's closure the files its readers name.
DATA = {
    "gates/doctor.sh": ("hooks/*", "retired_hooks.sha256", "docs/config/*"),
    "lib/hook_currency.py": ("hooks/*", "retired_hooks.sha256"),
    # Its `goh / "hooks" / HOOK` is the pre-push alone: spelled whole here, this file would NAME the
    # hook, and every closure holding it would follow the hook's runs as code (source_closure).
    "lib/push_gated.py": ("hooks/*", "retired_hooks.sha256"),
}


def data_files(root: Path, readers: tuple[str, ...] | None = None) -> list[str]:
    """The DATA files under root that `readers` (None: every reader) read, root-relative, each an
    existing file or link: a stock hook added under hooks/ is one more without a word here."""
    out: set[str] = set()
    for reader in DATA if readers is None else readers:
        for pat in DATA.get(reader, ()):
            if pat.endswith("/*"):
                top = root / pat[:-2]
                out.update(f"{pat[:-2]}/{p.name}" for p in (top.iterdir() if top.is_dir() else ())
                           if p.is_file() or p.is_symlink())  # fmt: skip
            elif (root / pat).is_file() or (root / pat).is_symlink():
                out.add(pat)
    return sorted(out)


# A variable ONE file declares as its own directory: `<file>` -> the names. gates/_goh_bin.sh's
# `here="${live:-$me}/gates"` is this root's gates/ (`me`), or, run from an export, the live
# checkout's (lib/goh_root.sh goh_live_root_for): the same goh, keyed here by this root's copy.
FILE_ROOTS = {"gates/_goh_bin.sh": ("here",)}

OWN = "the gated repo's own file (`git rev-parse --show-toplevel`): its tree keys it"
TUI = "tui/lib.sh: the gated repo's own or goh's, which the closure holds (tui/lib.sh)"
GATESRC = (
    "the gated repo's own .gatesrc: its tree keys a step, and its GOH_COV_* are the coverage key's"
    " `cov` lines (gates/_rust_proven.sh)"
)
HEAD_COPY = "HEAD's export of the very script running (C4): its bytes, the closure's own file"
GATESRC_ARG = "a `bash -c` sourcing $1, handed the gated repo's own .gatesrc: one key of it"
# A script path or `source` word through no declared root that the readers know (`<file>: <form>`,
# a source as `<file>: . <word>` -> what it reaches and why that is keyed): a script path is
# followed by its tail (`guess`), never refused. The coverage key (lib/rust_cov_scripts.py
# DECLARED) and the step closure (run_closure) read this one table.
DECLARED = {
    # A `source` word no reading places (source_holes).
    "gates/_common.sh: . $_goh_tui": TUI,
    "gates/coverage_gate.sh: . $_tui": TUI,
    "gates/local_ci.sh: . $_tui": TUI,
    "gates/_from_head.sh: . $_goh_copy": HEAD_COPY,
    "gates/_push_refusals.sh: . $1": GATESRC_ARG,
    "gates/commit_msg.sh: . $1": GATESRC_ARG,
    "gates/push_gate.sh: . $1": GATESRC_ARG,
    "lib/push_runs.sh: . $1": GATESRC_ARG,
    "gates/_push_refusals.sh: . $root/.gatesrc": GATESRC,
    "gates/cad_gate.sh: . ./.gatesrc": GATESRC,
    "gates/local_ci.sh: . $ROOT/.gatesrc": GATESRC,
    "gates/proven.sh: . $top/.gatesrc": GATESRC,
    "gates/push_gate.sh: . ./.gatesrc": GATESRC,
    "gates/py_gate.sh: . ./.gatesrc": GATESRC,
    "gates/py_staged.sh: . ./.gatesrc": GATESRC,
    "gates/round.sh: . ./.gatesrc": GATESRC,
    "gates/rust_each_crate.sh: . ./.gatesrc": GATESRC,
    "gates/rust_gate.sh: . ./.gatesrc": GATESRC,
    "gates/staged_guards.sh: . ./.gatesrc": GATESRC,
    "gates/structural.sh: . ./.gatesrc": GATESRC,
    "gates/swift_gate.sh: . ./.gatesrc": GATESRC,
    "gates/swift_staged.sh: . ./.gatesrc": GATESRC,
    "lib/landed_only.sh: . ./.gatesrc": GATESRC,
    "tools/land.sh: . ./.gatesrc": GATESRC,
    "tools/release-kit/version_step.sh: . $_vtop/.gatesrc": GATESRC,
    "tools/release-kit/version_step.sh: . ./.gatesrc": GATESRC,
    "tools/release_goh.sh: . ./.gatesrc": GATESRC,
    # A script path through no declared root.
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
    "gates/structural.sh: $_goh_inst/install.sh": "the live installer, only printed in a message",
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


def script_paths(path: Path, goh: Path, root: Path | None = None) -> list[tuple[str, Path | None]]:
    """(the form as `$VAR/tail`, the file it names or None for a hole) for each distinct script
    path in a shell file's code, comments left out. A FILE_ROOTS file is known by its path in the
    tree it is read from (`root`, else goh): a land reads its worktree with GOH_DIR an export, and
    keyed by goh's path alone gates/_goh_bin.sh's `$here` was a hole there, so the coverage
    identity was refused and every land measured again (GOH.FULLKEYSCRIPTS)."""
    here = path.parent
    text = path.read_text(encoding="utf-8", errors="replace")
    vars_ = source_closure.assigned(text, here, goh)
    tree = root if root is not None else goh
    rel = path.relative_to(tree).as_posix() if path.is_relative_to(tree) else ""
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


def _placed(word: str, here: Path, root: Path) -> bool:
    """Whether a source word names a file a reading keys or judges: one source_closure places, a
    script path (judged by script_paths), or a rooted path to a goh code file."""
    if source_closure._resolve(word, root / here, root, {}) is not None:
        return True
    m = ROOTED.match(word)
    if m is None or m.end() != len(word):
        return False
    return m.group(2).endswith(SCRIPT) or bool(guess(m.group(2), here, root))


def source_holes(rel: str, root: Path) -> list[str]:
    """`<rel>: . <word>` for each `source`/`.` word of the shell file root/rel that no reading
    places, comments left out: a hole, declared (DECLARED) or not."""
    here, out = Path(rel).parent, []
    for raw in (root / rel).read_text(encoding="utf-8", errors="replace").splitlines():
        line = source_closure._strip_comment(raw)
        for m in source_closure._SOURCE.finditer(line):
            word = source_closure._word(line, m.end())
            if not _placed(word, here, root):
                out.append(f"{rel}: . {word}")
    return list(dict.fromkeys(out))


def run_closure(seeds: set[str], root: Path, goh: Path, named_edges: bool = False) -> set[str]:
    """seeds and every file under root they source, import (source_closure.closure) or run through
    a declared root; with `named_edges`, every file their code names by path too. Raises
    LookupError on a hole DECLARED does not name, a script path (GOH.STEPCLOSUREREFUSE) or a
    `source` word (source_holes, GOH.FULLKEYSCRIPTS): no key can follow it. A DATA reader adds the
    data it reads (GOH.STEPDATAKEY). A declared root to
    no file is no edge here (the closure is read again from the next tree, so the file is keyed
    once it exists); the coverage key refuses one (lib/rust_cov_scripts.py)."""
    files = set(seeds)
    while True:
        found, _ = source_closure.closure(sorted(files), root, goh, named_edges)
        grown, holes = set(found), []
        for rel in found:
            if not source_closure._is_shell(root / rel):
                continue
            holes += [h for h in source_holes(rel, root) if h not in DECLARED]
            for form, target in script_paths(root / rel, goh, root):
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
        if grown <= files:  # a reader's data, never read for edges: a stock hook is git's to run
            return files | set(data_files(root, tuple(r for r in files if r in DATA)))
        files |= grown
