"""docs/map.md's mechanical part, generated (GOH.TABLEGEN): a row per script, none for a gone one.

WHY. `tests/test_wiring.py` demanded a hand-written row for every script under gates/, checks/ and
lib/, and lanes learned it at land (GOH.ENQUEUEVANISH's enqueue went red on a missing row,
2026-11-23). The rows' prose stays the author's; what the tree already holds is written here:

  render(root, text)   the map with a row added for each script no row names (at the end of its
                       section's table: the script, its docstring's first line, the tree's files
                       that name it, the tests that name it), every row naming a gone file of its
                       section dropped, a header-wide row's missing Invoked cell derived the same
                       way, and each table's delimiter row directly under its header.
  faults(text)         each row whose cell count differs from its table's header (GOH.MAPROWCOLS:
                       a parser splitting on `|` read a short row silently), which no generator
                       can fix: a `|` inside a cell is written `\\|`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# Section heading prefix -> (directory, the scripts that need a row there), as test_wiring's rule.
SECTIONS = {
    "## Gates": ("gates", ("*.py", "*.sh")),
    "## Checks": ("checks", ("check_*",)),
    "## Lib": ("lib", ("*.py",)),
}
CALLER_DIRS = ("gates", "checks", "lib", "tools", "hooks", "bin", "scripts", "crates")
SKIP_DIRS = {"target", ".build", "node_modules", "__pycache__", "by-stamp"}
PIPE = re.compile(r"(?<!\\)\|")
ROW_NAME = re.compile(r"^\| `([^`/]+\.(?:py|sh|tsv|json))`")
DELIM = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
NO_CALLER = "no file of the tree names it"
NO_TEST = "no test names it"


def cells(line: str) -> int:
    return len(PIPE.findall(line)) - 1


def _cell(text: str) -> str:
    return PIPE.sub(r"\\|", " ".join(text.split()))


def purpose(path: Path) -> str:
    """The first line a script says about itself: a module docstring, else its first comment."""
    text = path.read_text(encoding="utf-8", errors="replace")
    line = ""
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            doc = ""
        line = doc.strip().split("\n\n", 1)[0].replace("\n", " ")
    if not line:
        for raw in text.splitlines():
            s = raw.strip()
            if s.startswith("#") and not s.startswith(("#!", "# shellcheck", "# goh-")):
                line = s.lstrip("#").strip()
                break
    line = re.sub(rf"^(?:\S*{re.escape(path.name)})\s*(?:--|:|-)\s*", "", line)
    return _cell(line) if line else "no docstring or header comment says what it does"


def _walk(root: Path, dirs: tuple[str, ...]) -> list[Path]:
    out = []
    for d in dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts):
                out.append(p)
    return out


def _names(stem: str, name: str) -> re.Pattern[str]:
    s = re.escape(stem)
    return re.compile(rf"\b{re.escape(name)}\b|^\s*import {s}\b|^\s*from {s} import\b|"
                      rf"^\s*from lib import [^\n]*\b{s}\b|\blib\.{s}\b", re.MULTILINE)  # fmt: skip


class _Reader:
    """The tree's caller and test files, read on first need, once per render."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._callers: dict[Path, str] | None = None
        self._tests: dict[Path, str] | None = None

    @property
    def callers(self) -> dict[Path, str]:
        if self._callers is None:
            self._callers = {p: p.read_text(encoding="utf-8", errors="replace")
                             for p in _walk(self.root, CALLER_DIRS)
                             if p.suffix in (".py", ".sh", ".rs", "")}  # fmt: skip
        return self._callers

    @property
    def tests(self) -> dict[Path, str]:
        if self._tests is None:
            self._tests = {p: p.read_text(encoding="utf-8", errors="replace")
                           for p in sorted((self.root / "tests").glob("test_*.py"))}  # fmt: skip
        return self._tests

    def invoked(self, path: Path) -> str:
        rx = _names(path.stem, path.name)
        found = [f"`{p.relative_to(self.root)}`" for p, t in self.callers.items()
                 if p != path and path.stem in t and rx.search(t)]  # fmt: skip
        return (
            ", ".join(found[:4]) + (f" and {len(found) - 4} more" if len(found) > 4 else "")
            or NO_CALLER
        )

    def tested(self, path: Path) -> str:
        rx = _names(path.stem, path.name)
        found = [f"`{p.name}`" for p, t in self.tests.items() if path.stem in t and rx.search(t)]
        return (
            ", ".join(found[:3]) + (f" and {len(found) - 3} more" if len(found) > 3 else "")
            or NO_TEST
        )


def _sections(lines: list[str]) -> list[tuple[str, int, int]]:
    """(heading prefix, first line index, end index) of each generated section."""
    heads = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    out = []
    for n, i in enumerate(heads):
        for prefix in SECTIONS:
            if lines[i].startswith(prefix):
                out.append((prefix, i, heads[n + 1] if n + 1 < len(heads) else len(lines)))
    return out


def normalise(text: str) -> str:
    """Each table's delimiter row directly under its header, and only there."""
    lines, out, header = text.split("\n"), [], None
    for ln in lines:
        if not ln.startswith("|"):
            header = None
            out.append(ln)
            continue
        if header is None:
            header = len(out)
            out.append(ln)
            continue
        if DELIM.match(ln):
            continue
        if header == len(out) - 1:
            out.append("|" + "---|" * cells(out[header]))
        out.append(ln)
    return "\n".join(out)


def render(root: Path, text: str) -> str:
    lines = normalise(text).split("\n")
    reader = _Reader(root)
    for prefix, start, end in reversed(_sections(lines)):
        base, globs = SECTIONS[prefix]
        first = next((i for i in range(start, end) if lines[i].startswith("|")), None)
        if first is None:
            continue
        rows = [first]  # the section's first table: its contiguous run of rows
        while rows[-1] + 1 < end and lines[rows[-1] + 1].startswith("|"):
            rows.append(rows[-1] + 1)
        width = cells(lines[rows[0]])
        keep = []
        for i in rows[2:]:
            m = ROW_NAME.match(lines[i])
            if m and not (root / base / m.group(1)).exists():
                continue
            parts = PIPE.split(lines[i])
            if width == 4 and len(parts) - 2 == 3 and m:  # the Invoked cell, derived
                parts.insert(3, f" {reader.invoked(root / base / m.group(1))} ")
            keep.append("|".join(parts))
        scripts = sorted({p for g in globs for p in (root / base).glob(g) if p.is_file()})
        whole = "\n".join(lines)  # a script any row names has one (`a.py`, `b.sh` rows)
        for p in scripts:
            if f"`{p.name}`" not in whole:
                cols = [f"`{p.name}`", purpose(p)]
                cols += [reader.invoked(p)] if width == 4 else []
                keep.append("| " + " | ".join([*cols, reader.tested(p)]) + " |")
        lines[rows[0] + 2 : rows[-1] + 1] = keep
    return "\n".join(lines)


def faults(text: str, name: str = "docs/map.md") -> list[str]:
    out, header = [], None
    for n, ln in enumerate(text.split("\n"), 1):
        if not ln.startswith("|"):
            header = None
            continue
        if header is None:
            header = cells(ln)
        elif cells(ln) != header:
            out.append(f"{name}:{n}: {cells(ln)} cells, its table's header has {header}")
    return out
