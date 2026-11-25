"""Where a Rust v0 symbol's code sits: its crate and module path (GOH.BINREACH).

lib/bin_reach.py reads each stored test profile's entered functions (`llvm-profdata show
--all-functions`), which name them by their v0 mangling
(https://doc.rust-lang.org/rustc/symbol-mangling/v0.html). `home` parses just enough of the grammar
to give the module a function's code lives in -- an impl's methods where the impl is, a closure in
its function's module, a generic by its definition, a default trait method by its trait -- and
None for anything it cannot read, which its caller reads as "unknown, choose the test". Pinned by
tests/test_bin_reach.py.

GOH.BINREACH: the module a Rust v0 symbol's code sits in (an impl's methods where the impl is,
closures, generics, backrefs), None for what it cannot read.
"""

from __future__ import annotations

import re

UNKNOWN = "?"  # a profile symbol that did not parse: its test is always chosen
B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
FN_LINE = re.compile(r"^  (\S.*):$")
COUNT = re.compile(r"^    Function count: (\d+)$")


class _V0:
    """Just enough of the v0 grammar to find where a symbol's code sits; ValueError otherwise."""

    BASIC = frozenset("abcdefhijlmnopstuvxyz")

    def __init__(self, s: str) -> None:
        self.s, self.i = s, 0

    def peek(self) -> str:
        if self.i >= len(self.s):
            raise ValueError("truncated")
        return self.s[self.i]

    def take(self) -> str:
        c = self.peek()
        self.i += 1
        return c

    def b62(self) -> int:
        if self.peek() == "_":
            self.i += 1
            return 0
        n = 0
        while (c := self.take()) != "_":
            n = n * 62 + B62.index(c)
        return n + 1

    def number(self) -> int:
        j = self.i
        if self.peek() == "0":  # "0" is a whole number: "00" is two of them
            self.i += 1
            return 0
        while self.i < len(self.s) and self.s[self.i].isdigit():
            self.i += 1
        return int(self.s[j : self.i])

    def ident(self) -> str:
        if self.peek() == "s":
            self.i += 1
            self.b62()
        return self.bare()

    def bare(self) -> str:
        if self.peek() == "u":
            self.i += 1
        n = self.number()
        if self.peek() == "_":
            self.i += 1
        out = self.s[self.i : self.i + n]
        self.i += n
        return out

    def at(self, pos: int, fn):
        was, self.i = self.i, pos
        try:
            return fn()
        finally:
            self.i = was

    def path(self) -> tuple[str, list[tuple[str, str]]]:
        """(crate, [(namespace, ident)...]) of the code's home."""
        c = self.take()
        if c == "C":
            return self.ident(), []
        if c == "N":
            ns = self.take()
            crate, segs = self.path()
            return crate, [*segs, (ns, self.ident())]
        if c in "MX":
            if self.peek() == "s":
                self.i += 1
                self.b62()
            home = self.path()
            self.type()
            if c == "X":
                self.path()
            return home
        if c == "Y":
            self.type()
            return self.path()
        if c == "I":
            home = self.path()
            while self.peek() != "E":
                self.arg()
            self.i += 1
            return home
        if c == "B":
            return self.at(self.b62(), self.path)
        raise ValueError(f"path tag {c!r}")

    def arg(self) -> None:
        c = self.peek()
        if c == "L":
            self.i += 1
            self.b62()
        elif c == "K":
            self.i += 1
            self.const()
        else:
            self.type()

    def const(self) -> None:
        c = self.peek()
        if c == "p":
            self.i += 1
        elif c == "B":
            self.i += 1
            self.b62()
        else:
            self.type()
            if self.peek() == "n":
                self.i += 1
            while self.take() != "_":
                pass

    def type(self) -> None:
        c = self.peek()
        if c in self.BASIC:
            self.i += 1
        elif c in "CMXYNI":
            self.path()
        elif c == "B":
            self.i += 1
            self.b62()
        elif c == "A":
            self.i += 1
            self.type()
            self.const()
        elif c in "SPO":
            self.i += 1
            self.type()
        elif c in "RQ":
            self.i += 1
            if self.peek() == "L":
                self.i += 1
                self.b62()
            self.type()
        elif c == "T":
            self.i += 1
            while self.peek() != "E":
                self.type()
            self.i += 1
        elif c == "F":
            self.i += 1
            self.binder()
            if self.peek() == "U":
                self.i += 1
            if self.peek() == "K":
                self.i += 1
                if self.peek() == "C":
                    self.i += 1
                else:
                    self.bare()
            while self.peek() != "E":
                self.type()
            self.i += 1
            self.type()
        elif c == "D":
            self.i += 1
            self.binder()
            while self.peek() != "E":
                self.path()
                while self.peek() == "p":
                    self.i += 1
                    self.bare()
                    self.type()
            self.i += 1
            if self.peek() == "L":
                self.i += 1
                self.b62()
        else:
            raise ValueError(f"type tag {c!r}")

    def binder(self) -> None:
        if self.peek() == "G":
            self.i += 1
            self.b62()


def home(symbol: str) -> str | None:
    """`crate::a::b` -- the crate and the type-namespace segments down to the first value or
    closure -- of a v0 symbol (a local one's `file:` prefix dropped); None for none it can read."""
    sym = symbol.rsplit(":", 1)[-1] if ":" in symbol else symbol
    if not sym.startswith("_R"):
        return None
    p = _V0(sym[2:])
    try:
        if p.peek().isdigit():
            p.number()
        crate, segs = p.path()
    except (ValueError, IndexError):
        return None
    mods = []
    for ns, ident in segs:
        if ns != "t":
            break
        mods.append(ident)
    return "::".join([crate, *mods])


def homes(text: str) -> list[str]:
    """The homes of every function `llvm-profdata show --all-functions` printed as entered."""
    out: set[str] = set()
    name = None
    for line in text.splitlines():
        if m := FN_LINE.match(line):
            name = m.group(1)
        elif (m := COUNT.match(line)) and name is not None:
            if int(m.group(1)) > 0:
                out.add(home(name) or UNKNOWN)
            name = None
    return sorted(out)
