"""Which goh subcommands a change to the binary's source can reach (GOH.AFFECTEDBINARY2).

GOH.AFFECTEDBINARY made a change under crates/ choose every test that runs the binary: 425 entries,
283 whole files, 12m02s at load 120, past the pre-step's 10-minute deadline, so no Rust land got a
pre-step verdict. A scanner's change reaches only the subcommands whose code reaches the scanner, so
this reads two graphs from the crate itself, never a hand list:

  * the MODULE graph, one node per source file (a module: `src/x.rs`, `src/x/y.rs`, a `#[path]`
    child): a file depends on the module of every path its code resolves (`crate::x::f`,
    `super::f`, `child::f`, a name a `use` bound, a glob's module), comments and literals blanked.
    A path ending in an upper-case name (`skills::DEFAULT_MIN_SKILLS`, clap's defaults in cli_args)
    reads a CONSTANT: the module is reached, the code it calls is not. An `impl` of a type or trait
    defined in another file is reached from that file (the method runs through its value);
  * the DISPATCH table: main.rs's match arms, `Commands::Variant ... =>`, each with the modules it
    calls, directly or through a main.rs helper (`paths`, `declared`, `house_script`); a variant's
    subcommand is its kebab-case name, the way clap derives it (cli.rs).

A path whose reach cannot be told chooses EVERY subcommand (`subcommands` returns None): anything
under crates/ outside goh's src modules (Cargo.toml, build.rs, another crate, the step manifest,
main.rs, a file no `mod` declares), a module main() reaches before dispatch (cli, cli_args,
platform, goh_dir: every run parses its argv through them), and an `impl` naming no type or trait
of the crate (a blanket impl runs through values no edge follows). A file outside crates/ that a
non-test module `include_str!`s is its includer's (lib/busyloop_shapes.json is busyloop's).
"""

from __future__ import annotations

import os
import re
from functools import cached_property
from pathlib import Path

SRC = "crates/goh/src"
BUILD = ("crates", "Cargo.toml", "Cargo.lock", "rust-toolchain.toml")  # lib/gate_identity.py's
MAIN = f"{SRC}/main.rs"
CLI = f"{SRC}/cli.rs"
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
PATH = re.compile(r"(?<![\w.:])[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)+")
USE = re.compile(r"\buse\s+([^;]*);")
MOD = re.compile(r'(?:#\[path\s*=\s*"([^"]+)"\]\s*)?(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;')
INCLUDE = re.compile(r'\binclude(?:_str|_bytes)?!\(\s*"([^"]+)"\s*\)')
IMPL = re.compile(
    r"^[ \t]*(?:unsafe\s+)?impl\b\s*(?:<[^{>]*>)?\s*([^{;]*?)\s*(?:\bwhere\b[^{]*)?\{", re.MULTILINE
)
DEFINES = re.compile(r"\b(?:struct|enum|union|type|trait)\s+([A-Za-z_]\w*)")
FN = re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?fn\s+(\w+)", re.MULTILINE)
ARM = re.compile(r"\bCommands::([A-Z]\w*)|\bother\s*=>")
CONST = re.compile(r"^[A-Z][A-Z0-9_]*$")
Mod = tuple[str, ...]  # a module path: () is the crate root
Calls = tuple[set[str], set[str]]  # (files a run calls into, files it reads only a constant of)


def strip(text: str) -> str:
    """Rust `text` with comments and string, byte-string, raw-string and char literals blanked
    (newlines kept), so prose naming a module is no edge."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    out.append("\n" if text[i] == "\n" else "")
                    i += 1
        elif m := re.match(r'b?r(#*)"', text[i:]) if c in "br" and _boundary(text, i) else None:
            end = text.find('"' + m.group(1), i + m.end())
            end = n if end < 0 else end + 1 + len(m.group(1))
            out.append('""' + "\n" * text.count("\n", i, end))
            i = end
        elif c == '"' or (c == "b" and text.startswith('b"', i) and _boundary(text, i)):
            j = i + (2 if c == "b" else 1)
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append('""' + "\n" * text.count("\n", i, j))
            i = j + 1
        elif c == "'" and (m := re.match(r"'(?:\\u\{[0-9a-fA-F]+\}|\\.|[^'\\\n])'", text[i:])):
            out.append("' '")
            i += m.end()
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _boundary(text: str, i: int) -> bool:
    return i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")


def kebab(variant: str) -> str:
    """clap's derived subcommand name for `variant` (`LayerParity` -> `layer-parity`)."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", variant).lower()


def _body(code: str, start: int) -> tuple[int, int]:
    """(open, close) of the brace block whose `{` is the first at or after `start`."""
    i = code.index("{", start)
    depth = 0
    for j in range(i, len(code)):
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        if depth == 0:
            return i, j + 1
    return i, len(code)


class Crate:
    """goh's crate as read at `root`: the module graph and the dispatch table."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.texts: dict[str, str] = {}
        for dirpath, dirs, files in os.walk(root / SRC):
            dirs.sort()
            for name in sorted(files):
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                self.texts[rel] = path.read_text(encoding="utf-8", errors="replace")

    @cached_property
    def code(self) -> dict[str, str]:
        """{Rust source: its code, prose and literals blanked} (read on first use: `includes`
        needs none of it)."""
        return {rel: strip(t) for rel, t in self.texts.items() if rel.endswith(".rs")}

    @cached_property
    def files(self) -> dict[Mod, str]:
        """{module path: its source file}, from main.rs's `mod` declarations down."""
        out: dict[Mod, str] = {(): MAIN} if MAIN in self.code else {}
        todo: list[Mod] = list(out)
        while todo:
            here = todo.pop()
            rel = out[here]
            at = Path(rel).parent
            kids = at if Path(rel).name in ("main.rs", "mod.rs") else at / Path(rel).stem
            for path, name in MOD.findall(_uncommented(self.texts[rel])):
                tries = [at / path] if path else [kids / f"{name}.rs", kids / name / "mod.rs"]
                for t in tries:
                    child = os.path.normpath(t).replace(os.sep, "/")
                    if child in self.code and (*here, name) not in out:
                        out[(*here, name)] = child
                        todo.append((*here, name))
                        break
        return out

    @cached_property
    def module(self) -> dict[str, Mod]:
        return {rel: m for m, rel in self.files.items()}

    def _resolve(self, here: Mod, segs: list[str], bound: dict[str, Mod]) -> tuple[Mod, list[str]]:
        """(the deepest module `segs` names from `here`, the segments after it); () and all the
        segments when the first names no module (`std::fs`, `Vec::new`)."""
        head, rest = segs[0], segs[1:]
        if head == "crate":
            base: Mod | None = ()
        elif head in ("super", "self"):
            base = here[:-1] if head == "super" else here
            while head == "super" and rest[:1] == ["super"]:
                base, rest = base[:-1], rest[1:]
        else:
            base = bound.get(head)
        if base is None:
            return (), segs
        while rest and (*base, rest[0]) in self.files:
            base, rest = (*base, rest[0]), rest[1:]
        return base, rest

    def _scope(self, rel: str) -> tuple[dict[str, Mod], dict[str, str], set[str], list]:
        """(module names in scope, {imported item: its module's file}, the files `rel` globs
        from, its `use` leaves) for the file `rel`."""
        here = self.module[rel]
        bound = {m[-1]: m for m in self.files if m[:-1] == here and m}
        items: dict[str, str] = {}
        globs: set[str] = set()
        uses = [leaf for body in USE.findall(self.code[rel]) for leaf in _leaves(body)]
        for _ in range(2):  # a `use` may bind the name the next one starts from
            for segs, alias in uses:
                if segs[0] not in ("crate", "super", "self") and segs[0] not in bound:
                    continue
                target, rest = self._resolve(here, segs, bound)
                if not rest and target:
                    bound[alias or target[-1]] = target
                elif rest == ["*"]:
                    bound.update({m[-1]: m for m in self.files if m[:-1] == target and m})
                    globs.add(self.files[target])
                elif len(rest) == 1:
                    items[alias or rest[0]] = self.files[target]
        return bound, items, globs, uses

    def refs(self, rel: str, code: str | None = None, inside: frozenset = frozenset()) -> Calls:
        """(files `rel`'s code calls into, files it reads only a constant of); `code` stands in
        for the file's own (a dispatch arm of main.rs). A call to a main.rs function
        (`crate::goh_root()`) brings what that function calls, never all of main.rs."""
        here = self.module[rel]
        code = self.code[rel] if code is None else code
        bound, _, globs, uses = self._scope(rel)
        deep: set[str] = set(globs)
        shallow: set[str] = set()

        def item(target: Mod, rest: list[str], alias: str = "") -> None:
            if target == ():
                if rest and rest[0] in self.helpers and rest[0] not in inside:
                    more = self.refs(MAIN, self.helpers[rest[0]], inside | {rest[0]})
                    deep.update(more[0])
                    shallow.update(more[1])
            elif not rest:
                if alias:  # `use crate::x as y`: y's uses are not followed by name
                    deep.add(self.files[target])
            elif rest[0] != "*" and CONST.match(rest[0]):
                shallow.add(self.files[target])
            else:
                deep.add(self.files[target])

        for segs, alias in uses:
            target, rest = self._resolve(here, segs, bound)
            if target or segs[0] in ("crate", "super", "self"):
                item(target, rest, alias)
        for m in PATH.finditer(code):
            segs = [s.strip() for s in m.group(0).split("::")]
            target, rest = self._resolve(here, segs, bound)
            if target or segs[0] in ("crate", "super", "self"):
                item(target, rest)
        return deep - {rel, MAIN}, shallow - deep - {rel, MAIN}

    @cached_property
    def helpers(self) -> dict[str, str]:
        """{main.rs function that dispatches nothing: its code, signature included}."""
        code = self.code.get(MAIN, "")
        out = {}
        for m in FN.finditer(code):  # the signature's types name modules too
            text = code[m.start() : _body(code, m.end())[1]]
            if "Commands::" not in text and m.group(1) != "main":
                out[m.group(1)] = text
        return out

    def _impl_sources(self, rel: str) -> set[str] | None:
        """The files whose type or trait `rel` implements (a value of it runs `rel`'s method);
        None when an `impl` names neither a type of `rel` nor one any file of the crate defines."""
        defined = set(DEFINES.findall(self.code[rel]))
        bound, items, globs, _ = self._scope(rel)
        out: set[str] = set()
        for head in IMPL.findall(self.code[rel]):
            sides = [re.sub(r"<.*", "", side).strip() for side in re.split(r"\bfor\b", head)]
            local, found = False, set()
            for side in sides:
                segs = [x.strip() for x in side.lstrip("&").split("::") if x.strip()]
                if not segs:
                    continue
                if len(segs) == 1 and segs[0] in defined:
                    local = True
                elif len(segs) == 1 and segs[0] in items:
                    found.add(items[segs[0]])
                elif len(segs) > 1 and (t := self._resolve(self.module[rel], segs, bound))[0]:
                    found.add(self.files[t[0]])
                elif len(segs) == 1 and globs:
                    found |= globs
            if not (local or found):
                return None
            out |= found
        return out - {rel}

    @cached_property
    def edges(self) -> dict[str, Calls]:
        """{file: what it calls into, what it reads a constant of}, with each file that `impl`s a
        type or trait defined elsewhere reached from the definer."""
        out = {rel: self.refs(rel) for rel in self.module if rel != MAIN}
        for rel in out:
            for source in self._impl_sources(rel) or ():
                if source in out:
                    out[source][0].add(rel)
        return out

    @cached_property
    def foreign(self) -> set[str]:
        """Files with an `impl` naming neither a type of theirs nor one of the crate's
        (`impl<T: Read> Ext for T`): reached through values no edge follows."""
        return {rel for rel in self.module if rel != MAIN and self._impl_sources(rel) is None}

    def closure(self, calls: Calls) -> set[str]:
        """Every file a run entering `calls[0]` executes, and the files of the constants it reads
        (`calls[1]`, and theirs: a constant's initialiser names others' constants)."""
        deep, consts = calls
        seen = set(deep) | set(consts)
        todo = [(f, True) for f in deep] + [(f, False) for f in set(consts) - set(deep)]
        while todo:
            rel, runs = todo.pop()
            more, shallow = self.edges.get(rel, (set(), set()))
            for dep, dep_runs in [
                *((d, True) for d in more if runs),
                *((d, False) for d in shallow),
            ]:
                if dep not in seen:
                    seen.add(dep)
                    todo.append((dep, dep_runs))
        return seen

    @cached_property
    def variants(self) -> list[str]:
        """The `Commands` variants cli.rs declares, in order."""
        code = self.code.get(CLI, "")
        at = code.find("enum Commands")
        if at < 0:
            return []
        lo, hi = _body(code, at)
        body, depth, names = code[lo + 1 : hi - 1], 0, []
        for m in re.finditer(r"[{}()]|\b([A-Z]\w*)\b", body):
            tok = m.group(0)
            if tok in "{(":
                depth += 1
            elif tok in "})":
                depth -= 1
            elif (depth == 0 and not names) or (depth == 0 and _after_sep(body, m.start())):
                names.append(tok)
        return names

    @cached_property
    def dispatch(self) -> tuple[dict[str, Calls], Calls]:
        """({subcommand: what its arm calls into, with main.rs helpers followed}, what main.rs
        reaches outside every arm: main() and the helpers it calls before dispatching)."""
        code = self.code.get(MAIN, "")
        fns: dict[str, tuple[int, int]] = {}
        for m in FN.finditer(code):  # the span holds the signature: its types name modules too
            fns[m.group(1)] = (m.start(), _body(code, m.end())[1])
        dispatchers = {f for f, (lo, hi) in fns.items() if "Commands::" in code[lo:hi]}
        helpers = self.helpers

        def reach(text: str, seen: set[str]) -> Calls:
            deep, shallow = self.refs(MAIN, text)
            for f in sorted(set(IDENT.findall(text)) & set(helpers) - seen - {"main"}):
                seen.add(f)
                more = reach(helpers[f], seen)
                deep, shallow = deep | more[0], shallow | more[1]
            return deep, shallow

        entry: dict[str, Calls] = {}
        called: set[str] = set()
        for f in sorted(dispatchers):
            lo, hi = fns[f]
            marks = list(ARM.finditer(code, lo, hi))
            for k, m in enumerate(marks):
                if m.group(1):
                    seen: set[str] = set()
                    deep, shallow = reach(
                        code[m.start() : marks[k + 1].start() if k + 1 < len(marks) else hi], seen
                    )
                    was = entry.get(kebab(m.group(1)), (set(), set()))
                    entry[kebab(m.group(1))] = (was[0] | deep, was[1] | shallow)
                    called |= seen
        outside = code
        for f, (lo, hi) in sorted(fns.items(), key=lambda kv: -kv[1][0]):
            if f in dispatchers or f in called:
                outside = outside[:lo] + outside[hi:]
        return entry, reach(outside, set(dispatchers))

    @cached_property
    def shared(self) -> set[str]:
        """Modules every run reaches (main() and what it calls before dispatch), and the foreign."""
        return self.closure(self.dispatch[1]) | self.foreign | {MAIN}

    @cached_property
    def reaches(self) -> dict[str, set[str]]:
        """{subcommand: every module its run can reach}; a variant with no arm reaches none."""
        entry = self.dispatch[0]
        return {kebab(v): self.closure(entry.get(kebab(v), (set(), set()))) for v in self.variants}

    @cached_property
    def includes(self) -> dict[str, set[str]]:
        """{file outside crates/ that a non-test module `include_str!`s: the files including it}."""
        out: dict[str, set[str]] = {}
        for rel in self.texts:
            if not rel.endswith(".rs") or re.search(r"(?:^|_)tests\.rs$", Path(rel).name):
                continue
            for target in INCLUDE.findall(_uncommented(self.texts[rel])):
                path = os.path.normpath(Path(rel).parent / target).replace(os.sep, "/")
                if not path.startswith(("crates/", "..")):
                    out.setdefault(path, set()).add(rel)
        return out

    def subcommands(self, path: str) -> set[str] | None:
        """The subcommands a change to `path` can alter; None for every one; an empty set when
        `path` is no input of the binary at all."""
        files = self.includes.get(path) or ({path} if path in self.module else None)
        if files is None:
            return None if any(path == b or path.startswith(f"{b}/") for b in BUILD) else set()
        if files & self.shared:
            return None
        return {s for s, reach in self.reaches.items() if reach & files}


def _after_sep(body: str, at: int) -> bool:
    """A depth-0 capitalised word in an enum body starts a variant when it follows a `,` (or
    only attributes and doc comments, which `strip` blanked, since the last one)."""
    before = re.sub(r"#\[[^\]]*\]", "", body[:at]).rstrip()
    return before.endswith(",")


def _uncommented(text: str) -> str:
    """`text` with its line comments dropped (a `#[path = "..."]` keeps its literal)."""
    return re.sub(r"(?m)^\s*//.*$", "", text)


def _leaves(body: str) -> list[tuple[list[str], str]]:
    """The paths a `use` body imports, each with its alias: `a::{b, c::d as e, *}` gives
    (a::b, ''), (a::c::d, 'e'), (a::*, '')."""
    body = re.sub(r"^\s*pub(?:\([^)]*\))?\s+", "", body).strip()
    body = re.sub(r"^\s*use\s+", "", body)
    out: list[tuple[list[str], str]] = []

    def walk(prefix: list[str], text: str) -> None:
        depth, start, parts = 0, 0, []
        for i, c in enumerate(text):
            depth += {"{": 1, "}": -1}.get(c, 0)
            if c == "," and depth == 0:
                parts.append(text[start:i])
                start = i + 1
        parts.append(text[start:])
        for part in (p.strip() for p in parts):
            if not part:
                continue
            if "{" in part and "}" in part:
                lead = part[: part.index("{")].rstrip().rstrip(":").strip()
                inner = part[part.index("{") + 1 : part.rindex("}")]
                walk(prefix + [s.strip() for s in lead.split("::") if s.strip()], inner)
                continue
            m = re.match(r"(.*?)\s+as\s+(\w+)$", part)
            path, alias = (m.group(1), m.group(2)) if m else (part, "")
            segs = prefix + [s.strip() for s in path.split("::") if s.strip()]
            if segs and segs[-1] == "self":
                segs = segs[:-1]
            if segs:
                out.append((segs, alias))

    walk([], body.lstrip(":"))
    return out
