"""A goh script's dependency closure, read from its own sources -- never a hand list.

GOH.CLASS.HARNESS (2026-11-15). About 28 fixture items in a month were one shape: a fixture copied
a gate and not what the gate SOURCES. The GOH.BASH5SILENT guard broke four suites in one round
(bcb4016) because their fixtures carried a script without lib/bash5.sh, and the empty-scope
skeleton (abba237) answered with a second hand list (`GUARD = "lib/bash5.sh"`). A hand list is
right until the next `. "$HERE/../lib/x.sh"` line, which nothing compares it against.

`closure(paths, root)` follows, transitively:

  * a shell `source X` / `. X` statement, X resolved against the script's own directory when it is
    spelled `$(dirname "${BASH_SOURCE[0]}")/...` or `${BASH_SOURCE[0]%/*}/...`; against a variable
    the script assigns from its own directory (`HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
    pwd)"`), or chains from one (`ROOT="$(cd "$HERE/.." && pwd)"`); and `$GOH_DIR`, `$GOH_ROOT`,
    `$GOH` (when the script does not assign them) against the root. Any other variable is a
    consumer's tree or a runtime path, named unresolved, never guessed; so is a bare path (`.
    ./.gatesrc` reads the caller's working directory). An unassigned `$HERE` in a sourced sibling
    is its caller's directory, which in goh is the sibling's own;
  * a Python `import`/`from ... import`, a module found beside the file, at the root (`tui.lib`),
    or in lib/, checks/ or gates/ (the directories goh's scripts put on sys.path).

What it does NOT follow, so a fixture that needs one says so (`extra=`): a script a gate RUNS
rather than sources (`"$GOH_PY" "$HERE/check_x.py"`), a path built at runtime (`$PWD/...`,
`. "$_goh_copy"`), and the commands a script finds on PATH (`dirname`, test_doctor's shim PATH).
`unresolved` names every source line it read and could not place, so a reader sees the hole.

    copy_closure(dest, "tools/release_goh.sh")   # the script and what it sources, at their paths
"""

from __future__ import annotations

import ast
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_DIRS = ("", "lib", "checks", "gates")

# A path spelled from the script's own directory, once its quotes are gone.
_SELF_DIR = (
    re.compile(r"^\$\(\s*dirname\s+\$\{?BASH_SOURCE(?:\[0\])?\}?\s*\)/(.+)$"),
    re.compile(r"^\$\{BASH_SOURCE(?:\[0\])?%/\*\}/(.+)$"),
)
# `VAR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"`: VAR is the script's dir + "/..";
# `ROOT="$(cd "$HERE/.." && pwd)"` chains from a variable already placed.
_ASSIGN = re.compile(
    r"^(?:export\s+|readonly\s+|local\s+)?([A-Za-z_][A-Za-z0-9_]*)="
    r"\$\(\s*cd\s+(\$\(\s*dirname\s+\$\{?BASH_SOURCE(?:\[0\])?(?::-\$0)?\}?\s*\)|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)"
    r"(/[^&;)]*)?\s*&&\s*pwd"
)
# The names goh's scripts give the goh checkout itself (GOH_DIR by the house contract).
ROOT_VARS = ("GOH_DIR", "GOH_ROOT", "GOH")
# A sourced sibling that reads its caller's HERE (gates/_rust_proven.sh): goh's callers set it to
# their own directory, which is the sibling's.
# `_goh_d` is the bash 5 guard's own directory, found with builtins (`${BASH_SOURCE[0]%/*}`).
SELF_VARS = ("HERE", "_goh_d")
_SOURCE = re.compile(r"(?:^|[;&|{(]|\bthen|\bdo|\belse)\s*(?:source|\.)\s+(?=\S)")
_VAR = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}?/(.+)$")


def _word(line: str, start: int) -> str:
    """The shell word at `start`, quotes removed: `"$(dirname "${BASH_SOURCE[0]}")/x.sh"`."""
    out, quote, depth = [], "", 0
    for ch in line[start:]:
        if quote:
            if ch == quote:
                quote = ""
            else:
                out.append(ch)
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in " \t;&|":
            break
        out.append(ch)
    return "".join(out)


def _strip_comment(line: str) -> str:
    out, quote = [], ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t;"):
            break
        out.append(ch)
    return "".join(out)


def _is_shell(path: Path) -> bool:
    if path.suffix in (".sh", ".bash"):
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as fh:
            head = fh.readline(200)
    except OSError:
        return False
    return head.startswith(b"#!") and (b"sh" in head.split(b"/")[-1])


def _shell_deps(path: Path, root: Path, goh: Path) -> tuple[list[Path], list[str]]:
    here = path.parent
    text = path.read_text(encoding="utf-8", errors="replace")
    vars_: dict[str, Path] = {}
    for line in text.splitlines():
        m = _ASSIGN.match(line.strip().replace('"', ""))
        if not m:
            continue
        name, base, rel = m.group(1), m.group(2), (m.group(3) or "").strip()
        if base.startswith("$("):
            start: Path | None = here
        else:
            start = vars_.get(base.strip("${}"))
        if start is not None:
            vars_[name] = Path(os.path.normpath(start / ("." + rel)))
    deps, unresolved = [], []
    for raw in text.splitlines():
        line = _strip_comment(raw)
        for m in _SOURCE.finditer(line):
            token = _word(line, m.end())
            target = _resolve(token, here, goh, vars_)
            if target is None:
                unresolved.append(f"{path.relative_to(root)}: {raw.strip()}")
            else:
                deps.append(target)
    return deps, unresolved


def _resolve(token: str, here: Path, goh: Path, vars_: dict[str, Path]) -> Path | None:
    for pat in _SELF_DIR:
        m = pat.match(token)
        if m:
            return _existing([here / m.group(1)])
    m = _VAR.match(token)
    if m:
        name, rest = m.groups()
        if name in vars_:
            return _existing([vars_[name] / rest])
        if name in ROOT_VARS:
            return _existing([goh / rest])
        if name in SELF_VARS:
            return _existing([here / rest])
    # Any other variable is a consumer's tree or a runtime path; a bare path is the CALLER's
    # working directory (`. ./.gatesrc` reads the consumer's), never this tree's.
    return None


def _existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        c = Path(os.path.normpath(candidate))
        if c.is_file():
            return c
    return None


def _py_deps(path: Path, root: Path) -> list[Path]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    deps: list[Path] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                mod = (node.module or "").replace(".", "/")
                found = _module(base, mod) if mod else None
                deps.extend([found] if found else [])
                for a in node.names:
                    sub = _module(base, f"{mod}/{a.name}".lstrip("/"))
                    deps.extend([sub] if sub else [])
                continue
            names = [node.module or ""]
            names += [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            rel = name.replace(".", "/")
            for base in [path.parent, *(root / d for d in PY_DIRS)]:
                found = _module(base, rel)
                if found:
                    deps.append(found)
                    # `tui.lib` runs tui/__init__.py first
                    parts = rel.split("/")[:-1]
                    for i in range(1, len(parts) + 1):
                        init = _existing([base.joinpath(*parts[:i], "__init__.py")])
                        deps.extend([init] if init else [])
                    break
    return deps


def _module(base: Path, rel: str) -> Path | None:
    return _existing([base / f"{rel}.py", base / rel / "__init__.py"])


def direct(path: Path, root: Path = ROOT, goh: Path = ROOT) -> tuple[list[Path], list[str]]:
    """What `path` sources or imports, and the source lines it could not place."""
    if path.suffix == ".py":
        return _py_deps(path, root), []
    if _is_shell(path):
        return _shell_deps(path, root, goh)
    return [], []


def closure(paths, root: Path = ROOT, goh: Path = ROOT) -> tuple[list[str], list[str]]:
    """Every root-relative file `paths` need (themselves included), and what could not be placed.

    `goh` is the goh checkout `$GOH_DIR` names; a consumer's gate that sources goh's own files
    needs them from goh, not from its fixture, so they are outside `root` and left out."""
    root = Path(os.path.normpath(root))
    seen: dict[Path, None] = {}
    unresolved: list[str] = []
    todo = [Path(os.path.normpath(root / p)) for p in paths]
    while todo:
        p = todo.pop()
        if p in seen or not p.is_file():
            continue
        try:
            p.relative_to(root)
        except ValueError:
            continue  # outside the tree: a system file, not the fixture's to carry
        seen[p] = None
        deps, missing = direct(p, root, goh)
        unresolved += missing
        todo.extend(d for d in deps if d not in seen)
    return sorted(p.relative_to(root).as_posix() for p in seen), unresolved


def copy_closure(
    dest: Path, *paths: str, root: Path = ROOT, extra: tuple[str, ...] = ()
) -> list[str]:
    """Copy `paths` and their closure (plus `extra`, said by name) into `dest` at their paths."""
    files, _ = closure([*paths, *extra], root)
    for rel in files:
        target = Path(dest) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(root) / rel, target)
    return files


def sourced_from_outside(directory, skip=()) -> list[str]:
    """The shell files outside `directory` that its shell scripts source, relative to its parent:
    what a copy of the directory alone is missing at runtime (the empty-scope skeleton's gates)."""
    tree = Path(os.path.normpath(Path(directory).resolve().parent))
    name = Path(directory).resolve().name
    members = []
    for current, dirnames, files in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in skip]
        members += [os.path.relpath(Path(current, f).resolve(), tree) for f in files]
    found, _ = closure([m for m in members if not m.endswith(".py")], root=tree)
    return [rel for rel in found if not rel.startswith(f"{name}/") and not rel.endswith(".py")]
