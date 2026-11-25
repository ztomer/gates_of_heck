"""lib/map_table.py -- `goh map`: the script map, rendered from the tree on demand (GOH.MAPGEN).

    goh map                     # the whole inventory, as markdown (a row per file)
    goh map WORD...             # each file or `goh <check>` WORD names: its whole header, who runs
                                # it, which tests name it; exit 1 for a WORD that names nothing
    goh map --rev REV [WORD...] # the same, of a commit or tag
    goh map --check             # each file that says nothing of itself; exit 1 if any does

OWNER, 2026-11-24, asked how docs/map.md should stop causing land conflicts: "Render on demand".
The map was one committed table every lane edited, so lanes that shared nothing else conflicted in
it. What a file does is now said in that file only: a Python module's docstring, the comment block
at the top of a shell script or a Rust module. There is no copy to keep, so none to be stale.

THE HEADER. Its first paragraph is the file's row; `goh map WORD` prints all of it. A paragraph that
begins with a backticked path and a colon speaks for that path: a directory or a data file, which
has no header, or a file with no line to spare under its cap (it is found under the path it names
too). In a Rust module one that begins with a backticked `goh <check>` is that check's row.

WHO RUNS IT, WHICH TESTS NAME IT are read from the tree: callers from their code (a docstring or
comment that mentions a script is no call), tests from their whole text.

REFUSED (`faults`, tests/test_wiring.py): a script of MUST with no header, and a paragraph that
speaks for a path the tree does not hold.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import ast
import io
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "checks")]
import _prose_mask  # noqa: E402

TREE_READS = ("gates/", "checks/", "lib/", "tools/", "hooks/", "scripts/", "crates/", "tui/",
              "tests/", ".github/", "bin/", "install.sh", "docs/map.md")  # fmt: skip
# Section heading -> the directory its rows name, and the header row.
SECTIONS = (
    ("Gates (`gates/`)", "gates", "Script"),
    ("Checks (`checks/`)", "checks", "Script"),
    ("Lib (`lib/`)", "lib", "Module"),
)
CALLER_DIRS = ("gates", "checks", "lib", "tools", "hooks", "bin", "scripts", "crates")
TOOL_DIRS = ("tools", "scripts", "hooks", "tui", ".github/actions")
MUST = re.compile(r"(?:gates|lib|tools|scripts|hooks/claude)/[^/]+\.(?:py|sh)|checks/check_[^/]+")
NATIVE = re.compile(r"crates/[^/]+/src/(?:[^/]+|.+/mod)\.rs")
TOPS = (*CALLER_DIRS, "tui", ".github")
SKIP_DIRS = {"target", ".build", "node_modules", "__pycache__", "by-stamp", ".git"}
DIRECTIVE = re.compile(r"#\s*(?:shellcheck \w+=|goh-[\w-]+:)")
ENTRY = re.compile(r"(`[^`]+`(?:, `[^`]+`)*(?: \([^)]*\))?): ")
NO_CALLER = "no file of the tree names it"
NO_TEST = "no test names it"
TABLE_ROW = re.compile(r"^\|---", re.MULTILINE)


def _is_python(name: str, text: str) -> bool:
    return name.endswith(".py") or ("." not in name and "python" in text.split("\n", 1)[0])


def header(path: Path) -> str:
    """What the file says of itself at its top: a docstring, else its first comment block; ""."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if _is_python(path.name, text):
        try:
            return ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            return ""
    lead = "//" if path.suffix == ".rs" else "#"
    out: list[str] = []
    for n, raw in enumerate(text.splitlines()):
        if (n == 0 and raw.startswith("#!")) or (not out and not raw.strip()):
            continue
        if not raw.startswith(lead) or raw.startswith("#["):
            break
        if not DIRECTIVE.match(raw):
            out.append(re.sub(r"^(?://!|///?|#+) ?", "", raw).rstrip())
    return "\n".join(out).strip()


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def flat(text: str) -> str:
    return " ".join(text.split())


def purpose(head: str, name: str) -> str:
    """The file's row: its header's first paragraph, without a leading `name --`."""
    first = flat(paragraphs(head)[0]) if head.strip() else ""
    return re.sub(rf"^`?(?:[\w./-]*/)?{re.escape(name)}`?\s*(?:--|—|:)\s*", "", first)


def _cell(text: str) -> str:
    return flat(text).replace("|", "\\|")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip


def files(root: Path) -> list[str]:
    """Every file of the tree: git's (and what it would take), else a walk (a `--rev` export)."""
    if (root / ".git").exists():
        out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if out.returncode == 0:
            names = out.stdout.decode("utf-8", "replace").split("\0")
            return sorted({n for n in names if n and (root / n).is_file()})
    found = []
    for base, dirs, names in root.walk():
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [str((base / n).relative_to(root)) for n in names]
    return sorted(found)


def code(rel: str, text: str) -> str:
    """TEXT without its prose: a docstring or a comment naming a script is no call of it."""
    if rel.endswith(".rs"):
        return re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    if _is_python(Path(rel).name, text):
        return _prose_mask.python_code_only(text)
    return _prose_mask.shell_code_only(text)


class Tree:
    """One tree's files, headers and who names whom, each read once."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files = files(root)
        self.heads = {f: header(root / f) for f in self.files if self._says(f)}
        self._callers: dict[str, str] | None = None
        self._tests: dict[str, str] | None = None
        self._masked: dict[str, str] = {}

    @staticmethod
    def _says(f: str) -> bool:
        top, suffix = f.split("/", 1)[0], Path(f).suffix
        if top == "crates":
            return suffix == ".rs" and "/tests" not in f and "_tests" not in f
        scripted = suffix in (".py", ".sh", ".tsv", ".txt") or (suffix == "" and "/" in f)
        mapped = top in ("gates", "checks", "lib") or f.startswith(TOOL_DIRS)
        return f == "install.sh" or (mapped and scripted)

    def _read(self, f: str) -> str:
        return (self.root / f).read_text(encoding="utf-8", errors="replace")

    def _named_by(self, texts: dict[str, str], f: str, word: str = "") -> list[str]:
        p = Path(f)
        stem, s = p.parent.name if p.name == "mod.rs" else p.stem, re.escape(p.stem)
        rx = (
            rf"\b{re.escape(p.name)}\b|^\s*import {s}\b|^\s*from (?:\w+\.)*{s} import\b|"
            rf"\bfrom \w+ import [^\n]*\b{s}\b|\b(?:lib|gates|checks|tools)\.{s}\b|"
            rf"^ +(?:\w+, )*{s}(?:, \w+)*,?$|import_module\([\"']{s}[\"']\)"
        )
        if "_" in p.stem:  # no word of prose or of another module: its bare name is a use
            rx = rf"\b{s}\b"
        if p.suffix == ".rs":
            rx = rf"\bmod {re.escape(stem)};|\b{re.escape(stem)}::"
        if word:
            stem, rx = word, rf"\bgoh {re.escape(word)}\b|[\"']{re.escape(word)}[\"']"
        pat = re.compile(rx, re.MULTILINE)
        return [g for g, t in texts.items() if g != f and stem in t and pat.search(t)]

    def callers(self, f: str, word: str = "") -> list[str]:
        if self._callers is None:
            self._callers = {g: self._read(g) for g in self.files
                             if g.startswith(tuple(f"{d}/" for d in CALLER_DIRS))
                             and Path(g).suffix in (".py", ".sh", ".rs", "")}  # fmt: skip
        return [g for g in self._named_by(self._callers, f, word)
                if self._named_by({g: self._code(g)}, f, word)]  # fmt: skip

    def _code(self, f: str) -> str:
        if f not in self._masked:
            self._masked[f] = code(f, self._read(f))
        return self._masked[f]

    def tests(self, f: str, word: str = "") -> list[str]:
        if self._tests is None:
            self._tests = {g: self._read(g) for g in self.files
                           if re.fullmatch(r"tests/test_[^/]+\.py", g)}  # fmt: skip
        return [Path(g).name for g in self._named_by(self._tests, f, word)]

    def entries(self) -> list[tuple[str, list[str], str]]:
        """(the file that says it, what it speaks for, the paragraph), where that is not itself."""
        out = []
        for f, head in self.heads.items():
            for p in paragraphs(head):
                m = ENTRY.match(p)
                if not m:
                    continue
                subjects = re.findall(r"`([^`]+)`", m[1])
                top, below, _ = subjects[0].partition("/")
                native = f.endswith(".rs") and subjects[0].startswith("goh")
                spoken = below if top in TOPS else (self.root / subjects[0]).is_file()
                if native or ((spoken or subjects[0] == f"{top}/") and " " not in subjects[0]):
                    out.append((f, subjects, p))
        return out


def faults(root: Path) -> list[str]:
    tree = Tree(root)
    out = [
        f"{f}: no docstring or header comment says what it does"
        for f, head in tree.heads.items()
        if MUST.fullmatch(f) and not flat(head)
    ]
    for f, subjects, _ in tree.entries():
        if not subjects[0].startswith("goh") and not (root / subjects[0]).exists():
            out.append(f"{f}: speaks for {subjects[0]}, which the tree does not hold")
    return out


def _some(names: list[str], most: int, none: str) -> str:
    said = ", ".join(f"`{n}`" for n in names[:most]) or none
    return said + (f" and {len(names) - most} more" if len(names) > most else "")


def _row(*cols: str) -> str:
    return "| " + " | ".join(_cell(c) for c in cols) + " |"


def _native(tree: Tree) -> list[str]:
    checks: dict[str, list[tuple[str, str]]] = {}
    for f, subjects, p in tree.entries():
        if f.endswith(".rs"):
            body = ENTRY.sub("", p, count=1)
            for s in subjects:
                checks.setdefault(f, []).append((s, body))
    out = ["| Check | Source | Purpose | Test |", "|---|---|---|---|"]
    for f, head in tree.heads.items():
        if f in checks:
            for s, body in checks[f]:
                tests = tree.tests(f, s.removeprefix("goh").strip() or "goh")
                out.append(_row(f"`{s}`", f"`{f}`", body, _some(tests, 3, NO_TEST)))
        elif NATIVE.fullmatch(f) and flat(head):
            name = Path(f).parent.name if f.endswith("/mod.rs") else Path(f).stem
            out.append(_row(f"`{name}`", f"`{f}`", purpose(head, Path(f).name),
                            _some(tree.tests(f), 3, NO_TEST)))  # fmt: skip
    return out


def render(root: Path) -> str:
    """The inventory: a row per file, in the sections and the shape docs/map.md had."""
    tree = Tree(root)
    spoken = {s: p for _, subjects, p in tree.entries() for s in subjects}
    out = ["# The script map (`goh map`, rendered)", ""]
    for title, base, col in SECTIONS:
        out += [f"## {title}", "", f"| {col} | Purpose | Invoked | Test |", "|---|---|---|---|"]
        for f, head in tree.heads.items():
            name = f.removeprefix(f"{base}/")
            if f.startswith(f"{base}/") and (flat(head) or MUST.fullmatch(f)):
                said = purpose(head, Path(f).name) or "says nothing of itself"
                out.append(_row(f"`{name}`", said, _some(tree.callers(f), 4, NO_CALLER),
                                _some(tree.tests(f), 3, NO_TEST)))  # fmt: skip
        out.append("")
        if base == "gates":
            out += ["## Native checks (`bin/goh`, run as `gates/goh.sh <check>`)", ""]
            out += [*_native(tree), ""]
    out += ["## Tools", ""]
    for f, head in tree.heads.items():
        if (f == "install.sh" or f.startswith(TOOL_DIRS)) and flat(head):
            out.append(f"* `{f}` — {purpose(head, Path(f).name)}")
    for s, p in spoken.items():
        if not s.startswith("goh") and s not in tree.heads:
            out.append(f"* `{s}` — {flat(ENTRY.sub('', p, count=1))}")
    return "\n".join(out) + "\n"


def look(root: Path, words: list[str]) -> tuple[list[str], list[str]]:
    """(what `goh map WORD...` prints, the words nothing answers to)."""
    tree, out, missing = Tree(root), [], []
    entries = tree.entries()
    for word in words:
        w = word.removeprefix("goh ").strip("`")
        hits = [f for f in tree.heads if w in (f, Path(f).name, Path(f).stem)]
        said = [(f, s, p) for f, s, p in entries if w in s or f"goh {w}" in s
                or any(x in hits for x in s) or any(Path(x).name == w for x in s)]  # fmt: skip
        if not hits and not said:
            hits = [f for f in tree.heads if w in f]
        if not hits and not said:
            missing.append(word)
        for f in hits:
            out += [f"## {f}", "", tree.heads[f] or "says nothing of itself", ""]
            out += [f"Named by: {_some(tree.callers(f), 99, NO_CALLER)}"]
            out += [f"Tests: {_some(tree.tests(f), 99, NO_TEST)}", ""]
        for f, subjects, p in said:
            if f not in hits:
                out += [f"## {', '.join(subjects)} (said in {f})", "", p, ""]
    return out, missing


def at_rev(root: Path, rev: str, dest: Path) -> str:
    """REV's tree exported into DEST, or why it cannot be."""
    done = _git(root, "archive", rev)
    if done.returncode:
        return done.stderr.decode("utf-8", "replace").strip()
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return ""


def show(root: Path, words: list[str]) -> int:
    committed = root / "docs" / "map.md"  # a commit before GOH.MAPGEN holds the map itself
    text = committed.read_text(encoding="utf-8") if committed.is_file() else ""
    if TABLE_ROW.search(text):
        lines = [ln for ln in text.splitlines() if not words or any(w in ln for w in words)]
        print("\n".join(lines))
        return 0 if lines else 1
    if not words:
        print(render(root), end="")
        return 0
    out, missing = look(root, words)
    print("\n".join(out))
    for w in missing:
        print(f"goh map: nothing in the tree is named {w}", file=sys.stderr)
    return 1 if missing else 0


def main(argv: list[str]) -> int:
    from lib import goh_root, tmp_root  # an export answers for the checkout it runs for

    root = Path(goh_root.live_root_for(str(ROOT)) or ROOT)
    if argv[:1] in (["-h"], ["--help"]):
        print((__doc__ or "").split("\n\n")[1])
        return 0
    if argv[:1] == ["--check"]:
        found = faults(root)
        print("\n".join(found), end="\n" if found else "", file=sys.stderr)
        return 1 if found else 0
    if argv[:1] != ["--rev"]:
        return show(root, argv)
    if len(argv) < 2:
        print("usage: goh map --rev REV [WORD...]", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="goh-map-", dir=tmp_root.goh_tmp()) as tmp:
        why = at_rev(root, argv[1], Path(tmp))
        if why:
            print(f"goh map: {why}", file=sys.stderr)
            return 2
        return show(Path(tmp), argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
