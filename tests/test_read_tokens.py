"""What a test's read was of, and which of those a diff moves (GOH.LANDREADS, lib/read_tokens.py).

A read too wide is only slow: over 18 lands of 2026-11-25, 5,321 of 5,362 refused tests were refused
by a directory they had listed or that stood for many files. A read too narrow is a wrong green, so
each case below is asked both ways. In memory.
"""

from __future__ import annotations

import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import read_tokens as rt


def _moved(monkeypatch, status: dict[str, str], was: list[str], now: list[str]) -> rt.Moved:
    """`moved` of a diff git tells as STATUS, between the trees WAS and NOW."""

    def git(root, verb, *args):
        if verb == "diff":
            return "".join(f"{s}\0{p}\0" for p, s in status.items())
        return "".join(f"{p}\0" for p in (now if args[-1] == "HEAD" else was))

    monkeypatch.setattr(rt, "_git", git)
    return rt.moved(REPO_ROOT, "a" * 40, list(status))


LIB = ["lib/a.py", "lib/b.py", "lib/sub/__init__.py", "README.md"]
EDIT = ({"lib/a.py": "M"}, LIB, LIB)
ADD = ({"lib/new.py": "A"}, LIB, [*LIB, "lib/new.py"])
GO = ({"lib/a.py": "D"}, LIB, LIB[1:])
NEWDIR = ({"lib/deep/er/new.py": "A"}, LIB, [*LIB, "lib/deep/er/new.py"])
LASTGONE = ({"lib/sub/__init__.py": "D"}, LIB, [*LIB[:2], "README.md"])
TYPE = ({"lib/a.py": "T"}, LIB, LIB)


@pytest.mark.parametrize(
    ("diff", "hit", "not_hit"),
    [
        # an edit moves the file and what holds it whole, never a listing or a module's finding
        (EDIT, ["lib/a.py", "lib/", "./"], ["lib/*", "*", "import:a", "lib/b.py", "lib/sub/"]),
        # a new file: the names of its directory, the module it may now be found as
        (ADD, ["lib/*", "lib/new.py", "import:new", "import:lib.new", "lib/", "./"],
         ["*", "lib/a.py", "lib/sub/*", "import:ne", "import:lib"]),
        (GO, ["lib/*", "lib/a.py", "import:a", "import:lib.a"], ["*", "import:b"]),
        # a new directory is a new name in the one above it, up to one that stood before
        (NEWDIR, ["lib/deep/er/*", "lib/deep/*", "lib/*", "import:deep", "import:lib.deep.er.new"],
         ["*", "lib/sub/*", "import:lib"]),
        (LASTGONE, ["lib/sub/*", "lib/*", "import:sub", "import:lib.sub"], ["*", "import:lib"]),
        (TYPE, ["lib/a.py", "lib/*"], ["*"]),
        (({"new.md": "A"}, LIB, [*LIB, "new.md"]), ["*", "./", "new.md"], ["lib/*", "lib/"]),
    ],
    ids=["edit", "add", "go", "newdir", "lastgone", "type", "root"],
)  # fmt: skip
def test_a_diff_moves_what_it_changes_and_nothing_else(monkeypatch, diff, hit, not_hit) -> None:
    moved = _moved(monkeypatch, *diff)
    assert [w for w in hit if not moved.hit([w])] == []
    assert [w for w in not_hit if moved.hit([w])] == []


def test_a_diff_git_cannot_tell_moves_every_name_it_could() -> None:
    for moved in (rt.Moved(["lib/sub/a.py"]), rt.moved(REPO_ROOT, "c0" * 20, ["lib/sub/a.py"])):
        for word in ("lib/sub/a.py", "lib/sub/*", "lib/*", "*", "import:sub.a", "import:lib", "./"):
            assert moved.hit([word]) == "lib/sub/a.py", word
        assert not moved.hit(["lib/b.py", "tests/*", "import:b"])


def test_a_diff_git_tells_apart_from_the_callers_is_not_believed(monkeypatch) -> None:
    git = {"lib/a.py": "M"}
    monkeypatch.setattr(
        rt, "_git", lambda root, verb, *a: "M\0lib/a.py\0" if verb == "diff" else ""
    )
    assert rt.moved(REPO_ROOT, "a" * 40, list(git)).hit(["lib/*"]) == ""
    assert rt.moved(REPO_ROOT, "a" * 40, ["lib/b.py"]).hit(["lib/*"]) == "lib/b.py"
