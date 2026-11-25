"""lib/config_ref.py -- `goh config`: every `GOH_*` key, one file each, rendered (GOH.CONFIGMD).

    goh config                     # the whole reference, as markdown (a table per area)
    goh config KEY...              # each key's declaration, the code that reads it, the tests that
                                   # name it; exit 1 for a KEY nothing declares
    goh config --rev REV [KEY...]  # the same, of a commit or tag
    goh config --check             # what the gate refuses; exit 1 if anything

The reference was one 200 KB table every lane edited. A key is now declared in ONE file,
`docs/config/<KEY>.md` (named for the first key it declares), so two lanes that each add a key share
no file, and who reads it is computed from the code, never written down.

    # GOH_X, GOH_Y           every key the file declares
    Area: structural         a file docs/config/_areas/<area>.md
    Kind: setting            or internal (the gates' own, never set by hand), retired, external PATH
    Order: 120               optional: its place in the area; absent sorts last
    Default: unset
    (a blank line)
    What it means: one paragraph, a markdown table cell (a bare pipe is written as a table needs).

An area is `# <heading>`, `Order: N`, `Level: 0..3` (0: no heading is rendered), a blank line, then
its prose and its table's header verbatim; `<!-- rows -->` is where its keys' rows go, and
`<!-- rows to N -->` where those of Order N and below do (a row that declares no key stays between).

REFUSED (`faults`, tests/test_config_schema.py): a key the code reads and no file declares (a name
of PLACEHOLDERS is no key), a declared key no code reads, a file that breaks the form, and a
`.gatesrc.example` key that is no declared setting. Code is read with its prose blanked, and a list
of keys (LISTS, `*_free_keys.txt`) is no reader of them.
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

import re
import sys
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "checks")]
import _prose_mask  # noqa: E402

from lib import map_table  # noqa: E402

SCAN = ("gates", "checks", "lib", "tools", "hooks", "tui", "install.sh", "crates", "scripts",
        "bin", ".github")  # fmt: skip
TREE_READS = ("gates/", "checks/", "lib/", "tools/", "hooks/", "tui/", "install.sh", "crates/",
              "scripts/", "bin/", ".github/", "docs/config/", "docs/config.md", ".gatesrc.example",
              "tests/*.py")  # fmt: skip
KEY = re.compile(r"(?<!\w)GOH_[A-Z0-9_]*[A-Z0-9](?![\w*])")
# A name built at run time (`GOH_COV_FLOOR_$LANG`): a read of every declared key of its family.
BUILT = re.compile(r"(?<!\w)(GOH_[A-Z0-9_]+_)[$%{]")
SET = re.compile(r"(?m)^[#\s]*(?:export )?(GOH_[A-Z0-9_]*[A-Z0-9])=")
NAME = re.compile(r"GOH_[A-Z0-9_]*[A-Z0-9]")
KINDS = ("setting", "internal", "retired", "external")
ROWS = re.compile(r"<!-- rows(?: to (\d+))? -->")
FIELD = re.compile(r"([A-Z][a-z]+):(?: (.*))?")
LAST = 10**9
# A list of keys names them; it reads none.
LISTS = re.compile(
    r"const (?:PIPELINE_KEYS|OPT_IN_KEYS)\b.*?\];|^PLACEHOLDERS\b.*?^}", re.DOTALL | re.MULTILINE
)
# A Rust string or character, kept, else a comment, blanked.
RUST = re.compile(
    r'r(#+)".*?"\1|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])\'|(//[^\n]*|/\*.*?\*/)', re.DOTALL
)
# Names of the code that are no key: each a pattern or a fixture, and why. A stale one is refused.
PLACEHOLDERS: dict[str, str] = {
    "GOH_ACTION": "a Rust static's own name (crates/goh/src/gohpin/scan.rs): the action's pattern",
    "GOH_COV_FLOOR": "a pattern's prefix (gates/required_tools.py): any GOH_COV_FLOOR_<LANG>",
    "GOH_REPO": "a Rust static's own name (crates/goh/src/gohpin/scan.rs): the repo's pattern",
    "GOH_TAG": "a consumer workflow's own variable, judged as a pin (gohpin): goh reads none",
    "GOH_X": "a fixture line of a unit test inside crates/goh/src/localeprefix.rs",
}


@dataclass
class Decl:
    path: str
    keys: list[str]
    area: str
    kind: str
    order: int
    default: str
    text: str

    def row(self) -> str:
        names = ", ".join(f"`{k}`" for k in self.keys)
        return f"| {names} | {self.default} | {self.text} |"


@dataclass
class Area:
    slug: str
    heading: str
    order: int
    level: int
    body: list[str]


def _fields(text: str, known: tuple[str, ...]) -> tuple[str, dict[str, str], list[str], list[str]]:
    """(title, fields, body, what is wrong with the head) of one declaration or area file."""
    lines = text.rstrip("\n").split("\n")
    title, said, wrong = lines[0].removeprefix("# "), {}, []
    if not lines[0].startswith("# "):
        wrong.append("its first line is no `# ` title")
    head = lines.index("") if "" in lines else len(lines)
    for line in lines[1:head]:
        m = FIELD.fullmatch(line)
        if not m or m[1] not in known:
            wrong.append(f"no field of the form: {line!r}")
        elif m[1] in said:
            wrong.append(f"{m[1]} is said twice")
        else:
            said[m[1]] = (m[2] or "").strip()
    return title, said, lines[head + 1 :], wrong


def _number(said: Mapping[str, str], name: str, wrong: list[str], default: int) -> int:
    value = said.get(name, str(default))
    if not value.isdigit():
        wrong.append(f"{name} is no number: {value!r}")
        return default
    return int(value)


@dataclass
class Ref:
    """One tree's declarations and areas, with what breaks the form."""

    root: Path
    decls: list[Decl] = field(default_factory=list)
    areas: dict[str, Area] = field(default_factory=dict)
    broken: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        top = self.root / "docs" / "config"
        for p in sorted((top / "_areas").glob("*")):
            self._area(p)
        for p in sorted(p for p in top.glob("*") if p.is_file()):
            self._decl(p)
        owner: dict[str, str] = {}
        for d in self.decls:
            for k in d.keys:
                if k in owner:
                    self.broken.append(f"{d.path}: {k} is declared in {owner[k]} too")
                owner.setdefault(k, d.path)
        for slug in sorted({d.area for d in self.decls} & set(self.areas)):
            if "<!-- rows -->" not in self.areas[slug].body:
                self.broken.append(f"docs/config/_areas/{slug}.md: no `<!-- rows -->` for its keys")

    def _bad(self, p: Path, wrong: list[str]) -> None:
        self.broken += [f"{p.relative_to(self.root)}: {w}" for w in wrong]

    def _area(self, p: Path) -> None:
        title, said, body, wrong = _fields(p.read_text(encoding="utf-8"), ("Order", "Level"))
        wrong += [f"no {f}" for f in ("Order", "Level") if f not in said]
        level = _number(said, "Level", wrong, 2)
        if level > 3:
            wrong.append(f"Level is 0 to 3, not {level}")
        self._bad(p, wrong)
        self.areas[p.stem] = Area(p.stem, title, _number(said, "Order", [], LAST), level, body)

    def _decl(self, p: Path) -> None:
        known = ("Area", "Kind", "Order", "Default")
        title, said, body, wrong = _fields(p.read_text(encoding="utf-8"), known)
        keys = title.split(", ")
        wrong += [f"{k!r} is no GOH_ key" for k in keys if not NAME.fullmatch(k)]
        if p.name != f"{keys[0]}.md":
            wrong.append(f"declares {keys[0]} first: its name is {keys[0]}.md")
        if said.get("Area") not in self.areas:
            wrong.append(f"Area {said.get('Area')!r} is no file of docs/config/_areas/")
        kind, _, where = said.get("Kind", "").partition(" ")
        if kind not in KINDS or bool(where) != (kind == "external"):
            wrong.append("Kind is setting, internal, retired or `external PATH`")
        if not said.get("Default"):
            wrong.append("no Default")
        if not any(body) or not all(body):
            wrong.append("what it means is one paragraph after a blank line")
        order = _number(said, "Order", wrong, LAST)
        self._bad(p, wrong)
        rel, text = str(p.relative_to(self.root)), " ".join(body)
        self.decls.append(Decl(rel, keys, said.get("Area", ""), kind, order,
                               said.get("Default", ""), text))  # fmt: skip

    def by_key(self) -> dict[str, Decl]:
        return {k: d for d in reversed(self.decls) for k in d.keys}

    def render(self) -> str:
        out: list[str] = []
        for a in sorted(self.areas.values(), key=lambda a: (a.order, a.slug)):
            if a.level:
                out += ["#" * a.level + " " + a.heading, ""]
            rows = sorted((d for d in self.decls if d.area == a.slug),
                          key=lambda d: (d.order, d.keys))  # fmt: skip
            for line in a.body:
                m = ROWS.fullmatch(line)
                if not m:
                    out.append(line)
                    continue
                upto = int(m[1]) if m[1] else LAST
                out += [d.row() for d in rows if d.order <= upto]
                rows = [d for d in rows if d.order > upto]
            out.append("")
        return "\n".join(out).rstrip("\n") + "\n"


def code(rel: str, text: str) -> str:
    """TEXT without its prose: a comment or a docstring that names a key is no read of it."""
    text = LISTS.sub("", text)
    if rel.endswith(".rs"):
        return RUST.sub(lambda m: "" if m[2] else m[0], text)
    if rel.endswith((".json", ".mjs", ".js", ".ts")):
        return text
    if rel.endswith(".py") or ("." not in Path(rel).name and "python" in text.split("\n", 1)[0]):
        return _prose_mask.python_code_only(text)
    # A heredoc's body is kept: `python3 - <<'PY'` reads keys in it.
    return "\n".join(_prose_mask.strip_comment(line) for line in text.split("\n"))


def _scanned(rel: str) -> bool:
    p = Path(rel)
    if p.suffix in (".md", ".sha256") or rel.endswith("_free_keys.txt"):
        return False
    tested = "tests" in p.parts or p.stem == "tests" or p.stem.endswith("_tests")
    if p.parts[0] == "tests":  # what the suite runs on: a test that names a key plants it
        return len(p.parts) == 2 and p.suffix == ".py" and not p.name.startswith("test_")
    return (p.parts[0] in SCAN or rel in SCAN) and not (p.suffix == ".rs" and tested)


def reads(root: Path) -> dict[str, list[str]]:
    """Key -> the code files that read it (tests/ support modules too), by the masked code; a name
    built at run time is its prefix, which ends in `_`."""
    found: dict[str, list[str]] = {}
    for rel, raw in _texts(root, _scanned):
        if KEY.search(raw) or BUILT.search(raw):
            text = code(rel, raw)
            for k in sorted({*KEY.findall(text), *BUILT.findall(text)}):
                found.setdefault(k, []).append(rel)
    return found


def _texts(root: Path, wanted) -> Iterator[tuple[str, str]]:
    for rel in map_table.files(root):
        if wanted(rel):
            data = (root / rel).read_bytes()
            if b"GOH_" in data and b"\0" not in data:
                yield rel, data.decode("utf-8", "replace")


def _readers(key: str, found: Mapping[str, list[str]]) -> list[str]:
    built = [f for k, files in found.items() if k.endswith("_") and key.startswith(k)
             for f in files]  # fmt: skip
    return sorted({*found.get(key, []), *built})


def undeclared(root: Path, ref: Ref, found: Mapping[str, list[str]],
               placeholders: Mapping[str, str]) -> list[str]:  # fmt: skip
    """A key the code reads (a tests/ support module's is the suite's own) and no file declares."""
    known, out = ref.by_key(), []
    for k, files in sorted(found.items()):
        own = [f for f in files if not f.startswith("tests/")]
        family = k.endswith("_") and any(d.startswith(k) for d in known)
        if own and k not in known and k not in placeholders and not family:
            out.append(f"{k} is read by {', '.join(own[:3])} and declared nowhere: "
                       f"write docs/config/{k}.md (the form: docs/config.md)")  # fmt: skip
    for k in sorted(placeholders):
        if k in known or k not in found:
            why = "is declared" if k in known else "is in no code"
            out.append(f"lib/config_ref.py: PLACEHOLDERS names {k}, which {why}")
    return out


def dead(root: Path, ref: Ref, found: Mapping[str, list[str]]) -> list[str]:
    """A declared key no code reads; an external one's reader is outside this tree."""
    return [f"{d.path}: no code reads {k}; delete the declaration with its last reader"
            for d in ref.decls if d.kind != "external" for k in d.keys
            if not _readers(k, found)]  # fmt: skip


def example(root: Path, ref: Ref) -> list[str]:
    """A key the starter template sets that is internal or retired, or names undeclared."""
    path, known = root / ".gatesrc.example", ref.by_key()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    odd = {k for k in SET.findall(text) if k in known and known[k].kind in ("internal", "retired")}
    odd |= set(KEY.findall(text)) - set(known)
    return [f".gatesrc.example: {k} is no declared setting" for k in sorted(odd)]


def faults(root: Path, placeholders: Mapping[str, str] = PLACEHOLDERS) -> list[str]:
    ref, found = Ref(root), reads(root)
    return [*ref.broken, *undeclared(root, ref, found, placeholders), *dead(root, ref, found),
            *example(root, ref)]  # fmt: skip


def look(root: Path, words: list[str]) -> tuple[list[str], list[str]]:
    """(what `goh config KEY...` prints, the keys nothing declares)."""
    known, found, out, missing = Ref(root).by_key(), reads(root), [], []
    tests = dict(_texts(root, re.compile(r"tests/test_[^/]+\.py").fullmatch))
    for word in words:
        d = known.get(word)
        if d is None:
            missing.append(word)
            continue
        named = re.compile(rf"(?<!\w){word}(?!\w)")
        out += [f"## {d.path}", "", (root / d.path).read_text(encoding="utf-8").rstrip("\n"), ""]
        out += [f"Read by: {', '.join(_readers(word, found)) or 'no code of this tree'}"]
        hits = [Path(f).name for f, t in tests.items() if named.search(t)]
        out += [f"Tests: {', '.join(hits) or 'no test names it'}", ""]
    return out, missing


def show(root: Path, words: list[str]) -> int:
    committed = root / "docs" / "config.md"
    if not (root / "docs" / "config").is_dir() and committed.is_file():  # before GOH.CONFIGMD
        lines = [ln for ln in committed.read_text(encoding="utf-8").splitlines()
                 if not words or any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", ln)
                                     for w in words)]  # fmt: skip
        print("\n".join(lines))
        return 0 if lines else 1
    if not words:
        print(Ref(root).render(), end="")
        return 0
    out, missing = look(root, words)
    print("\n".join(out))
    for w in missing:
        print(f"goh config: no file of docs/config/ declares {w}", file=sys.stderr)
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
        print("usage: goh config --rev REV [KEY...]", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="goh-config-", dir=tmp_root.goh_tmp()) as tmp:
        why = map_table.at_rev(root, argv[1], Path(tmp))
        if why:
            print(f"goh config: {why}", file=sys.stderr)
            return 2
        return show(Path(tmp), argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
