"""lib/config_decls.py -- docs/config/'s declarations: each `GOH_*` key's file, each area's, and
the page they render (GOH.CONFIGMD; the form is lib/config_ref.py's header).

It reads docs/config/ alone. Who reads a key is asked of every code tree by lib/config_ref.py, so a
test that asks only what is declared is not chosen by a change to gates/.
Pinned by tests/test_goh_config.py.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

TREE_READS = ("docs/config/",)
NAME = re.compile(r"GOH_[A-Z0-9_]*[A-Z0-9]")
KINDS = ("setting", "internal", "retired", "external")
ROWS = re.compile(r"<!-- rows(?: to (\d+))? -->")
FIELD = re.compile(r"([A-Z][a-z]+):(?: (.*))?")
LAST = 10**9


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
