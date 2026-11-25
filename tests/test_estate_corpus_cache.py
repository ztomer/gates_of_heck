"""check_estate_corpus remembers a verified entry, and every way that memory could lie is pinned.

The sweep materialised every corpus on every run -- ~4000 file creations, 3.35 of its 4.6 CPU-s in
the kernel, and the work that serialized `structural --full` across sessions (roadmap 2.4,
tools/session_bench.py). A VERIFIED entry is now recorded under a key of everything its verdict
depends on (checks/_estate_cache.py): the entry itself, every corpus file's path, size and mtime,
the checker's own source, the native binary, the GOH_* environment. Written BEFORE the cache: each
test below is one way a hit could be wrong.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _tree_clone import clone_tree
from conftest import REPO_ROOT, native_goh_path
from test_proven import goh  # noqa: F401  (fixture: a committed copy of the gates)
from test_proven_goh_content import _export

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_estate_corpus as sweep

PLANT = "\n<<<<<<< HEAD\nlet value = 1\n=======\nlet value = 2\n>>>>>>> feature\n"


@pytest.fixture
def estate(tmp_path: Path, monkeypatch) -> dict:
    root = tmp_path / "estate"
    (root / "pkg").mkdir(parents=True)
    for i in range(6):
        (root / "pkg" / f"m{i}.py").write_text("".join(f"x{n} = {n}\n" for n in range(20)))
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
    monkeypatch.setenv("GOH_ESTATE_CACHE", str(tmp_path / "cache"))
    # Its own reuse audit (GOH.REUSEAUDIT): in a shared one the tenth hit of the suite -- a sampled
    # one, judged afresh -- lands in whichever test makes it, and a "materialises nothing" test
    # would see the corpus rebuilt.
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setenv("GOH_BIN", str(native_goh_path()))
    monkeypatch.setenv("GOH_DIR", str(REPO_ROOT))
    monkeypatch.delenv("GOH_PROVEN", raising=False)
    calls = []
    real = sweep.materialise
    monkeypatch.setattr(sweep, "materialise", lambda *a: (calls.append(a[2]), real(*a))[1])
    return {
        "root": root,
        "calls": calls,
        "entry": {
            "checker": "markers",
            "root": str(root),
            "scope": ("pkg",),
            "ext": ".py",
            "plant": PLANT,
            "args": (),
            "why": "fixture",
        },
    }


def _judge(estate: dict) -> str:
    return sweep.judge(dict(estate["entry"]), [])


def test_a_repeat_hits_and_each_thing_the_verdict_depends_on_is_a_miss(
    estate, tmp_path, monkeypatch
) -> None:
    """One estate, moved one thing at a time (GOH.PYSLOW4: each was a first verdict of its own):
    every move is a miss, and the same question again is a hit, so no move is carried by the last.
    """
    calls = estate["calls"]

    def builds() -> int:
        before = len(calls)
        assert _judge(estate) == "verified"
        return len(calls) - before

    assert builds(), "the first run must build the corpus"
    assert not builds(), "nothing moved, yet the corpus was rebuilt"
    path = estate["root"] / "pkg" / "m3.py"
    path.write_text(path.read_text() + "y = 1\n")
    assert builds() and not builds(), "an edit in the corpus"
    (estate["root"] / "pkg" / "new.py").write_text("z = 1\n")
    assert builds() and not builds(), "a new file in the corpus"
    other = tmp_path / "goh-copy"
    shutil.copy2(native_goh_path(), other)  # a clone (tests/_clone_copies.py)
    monkeypatch.setenv("GOH_BIN", str(other))
    assert builds() and not builds(), "a different binary"
    monkeypatch.setenv("GOH_MAX_LINES", "123")
    assert builds() and not builds(), "a different GOH_* environment"
    estate["entry"]["plant"] = PLANT + "\n"
    assert builds() and not builds(), "a different entry"
    for f in (tmp_path / "cache").iterdir():
        f.write_text("{not json")
    assert builds() and not builds(), "a corrupt record"
    monkeypatch.setenv("GOH_PROVEN", "0")
    assert builds() and builds(), "off never hits"


def test_the_checkers_own_source_is_in_the_key() -> None:
    from _estate_cache import identity

    base = {"source": b"a", "env": {}, "binary": ("goh", 1, 2)}
    assert identity(**base) != identity(**{**base, "source": b"b"})


def test_a_red_verdict_is_never_recorded(estate) -> None:
    """A checker that does not catch its plant: blind, and asked again next time."""
    estate["entry"]["plant"] = "\nno marker here\n"
    assert _judge(estate) == "blind"
    built = len(estate["calls"])
    assert _judge(estate) == "blind"
    assert len(estate["calls"]) > built


def test_an_absent_estate_is_never_a_hit(estate, tmp_path) -> None:
    estate["entry"]["root"] = str(tmp_path / "gone")
    assert _judge(estate) == "unavailable"
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").iterdir())


@pytest.mark.parametrize("value", ["0", "off", "OFF", "false", "no", "0/", "cache", "./cache"])
def test_an_off_word_or_a_relative_dir_is_never_a_cache_in_the_cwd(value, monkeypatch) -> None:
    """`GOH_ESTATE_CACHE=0` once meant "off" to its caller and "the directory 0" to this module, and a
    gate run in this repo committed eight verdicts under `0/`. An off word is off, and a relative
    path is refused: it names whatever directory the gate happens to run in."""
    import _estate_cache

    monkeypatch.delenv("GOH_PROVEN", raising=False)
    monkeypatch.setenv("GOH_ESTATE_CACHE", value)
    assert _estate_cache.cache_dir() is None


def test_an_absolute_cache_dir_is_kept(tmp_path: Path, monkeypatch) -> None:
    import _estate_cache

    monkeypatch.delenv("GOH_PROVEN", raising=False)
    monkeypatch.setenv("GOH_ESTATE_CACHE", str(tmp_path / "c"))
    assert _estate_cache.cache_dir() == str(tmp_path / "c")


def _goh_export(gates: Path, tmp_path: Path) -> Path:
    """The head export of the gates copy, as gates/_from_head.sh cuts one: the commit's files,
    `.goh-head`, and the launcher bin/goh every export gets a fresh copy of."""
    dest = _export(gates, tmp_path)
    (dest / "bin").mkdir()
    shutil.copy(gates / "gates" / "_export_bin_goh.sh", dest / "bin" / "goh")
    return dest


def _moved(export: Path, where: Path) -> Path:
    """The same export at another path: the next land's ~/.cache/goh/head/<sha>."""
    dest = where / export.name
    dest.parent.mkdir(parents=True)
    clone_tree(export, dest)
    return dest


def test_two_head_export_paths_of_one_content_share_one_identity(
    goh,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    """GOH.PROVENPYPATH: goh's land runs the tip's head export, a new path per commit, and the key
    held GOH_DIR's path and the export launcher's path and mtime: no land found the last one's
    records. The gates there are keyed by content now, and one changed gate byte still misses."""
    import _estate_cache

    for k in ("GOH_BIN", "GOH_RESOLVED_BIN", "GOH_RESOLVED_FOR", "GOH_LIVE"):
        monkeypatch.delenv(k, raising=False)
    src = str(REPO_ROOT / "checks" / "check_estate_corpus.py")
    a = _goh_export(goh, tmp_path)
    b = _moved(a, tmp_path / "head2")

    def ident(root: Path) -> str:
        monkeypatch.setenv("GOH_DIR", str(root))
        return _estate_cache.current_identity(src, str(root))

    assert ident(a) == ident(b)
    assert _estate_cache._binary(str(a)) == ("export bin/goh",), _estate_cache._binary(str(a))
    c = _moved(a, tmp_path / "head3")  # a new export: the identity is read once per process
    with open(c / "gates" / "goh.sh", "a") as f:
        f.write("# one byte more\n")
    assert ident(c) != ident(a)
