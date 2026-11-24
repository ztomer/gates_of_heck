"""The one verdict store and its reuse API (GOH.ONEVERDICT, lib/reuse.py).

koffee_big ship 20261115T121828Z-952ef800 spent 3137.5 s in its release step on two full gates: the
push gate had gated the handoff commit when the land queue pushed it, but wrote that verdict only
into a per-ship file, and only under `goh ship`, so the ship's own push ("nothing to gate") left
the release kit nothing to skip on. A verdict is now one record keyed by (repo, tree, gate
identity) that any caller asks through one API, and every calibration below is a MISS: a changed
tree, a changed gate, a partial key, an old record, a record under another key.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse  # lib/, after its path

REUSE = REPO_ROOT / "lib" / "reuse.py"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    for name in ("GOH_REUSE_TTL_S", "GOH_REUSE_NOW", "GOH_BIN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "store"


FIELDS = {"repo": "/r/.git", "tree": "t" * 40, "identity": "i" * 64}


def test_a_recorded_key_hits_and_each_changed_field_misses(store):
    reuse.record("gate", FIELDS, "green", "test")
    rec, why = reuse.lookup("gate", FIELDS)
    assert rec is not None and rec["verdict"] == "green", why
    for field, value in FIELDS.items():
        moved = {**FIELDS, field: value + "x"}
        assert reuse.lookup("gate", moved)[0] is None, field  # each field is in the key


def test_a_partial_or_unregistered_key_is_refused(store):
    with pytest.raises(KeyError):
        reuse.lookup("gate", {"repo": "/r/.git", "tree": "t" * 40})  # the tree alone: no gate
    with pytest.raises(KeyError):
        reuse.record("gate", {**FIELDS, "extra": "x"}, "green")
    with pytest.raises(KeyError):
        reuse.lookup("nonesuch", FIELDS)
    with pytest.raises(ValueError):
        reuse.register("gate", ("tree",), 1, "a second gate path")


def _body(loc: str, body: str | None = None) -> dict:
    """The record at LOC (reuse_store's `<db>#<path>/<key>`), its body replaced by BODY first."""
    db, _, rest = loc.partition("#")
    path, _, key = rest.partition("/")
    with sqlite3.connect(db) as con:
        if body is not None:
            con.execute("UPDATE verdicts SET body=? WHERE path=? AND key=?", (body, path, key))
        row = con.execute("SELECT body FROM verdicts WHERE path=? AND key=?", (path, key))
        row = row.fetchone()
    con.close()
    return json.loads(row[0]) if row and body is None else {}


def test_an_old_or_foreign_record_misses(store, monkeypatch):
    loc = reuse.record("gate", FIELDS, "green")
    monkeypatch.setenv("GOH_REUSE_NOW", str(int(_body(loc)["epoch"]) + 86400))
    assert "outside" in reuse.lookup("gate", FIELDS)[1]  # a day old: the TTL
    monkeypatch.delenv("GOH_REUSE_NOW")
    rec = _body(loc)
    rec["key"] = "0" * 64
    _body(loc, json.dumps(rec))
    assert "not this key's" in reuse.lookup("gate", FIELDS)[1]  # a record in the wrong slot
    _body(loc, "{")
    assert "unreadable" in reuse.lookup("gate", FIELDS)[1]


def test_a_record_older_than_its_ttl_is_pruned(store, monkeypatch):
    old = reuse.record("gate", FIELDS, "green")
    young = {**FIELDS, "tree": "u" * 40}
    monkeypatch.setenv("GOH_REUSE_NOW", str(int(_body(old)["epoch"]) + 86400))
    kept = reuse.record("gate", young, "green")
    assert reuse.prune("gate") == 1
    assert not _body(old) and _body(kept)


def recorded_hows(store: Path, sha: str) -> list[str]:
    """How each `gate` record of sha in the store was gated (a push gate test's view of it)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOH_REUSE_DIR", str(store))
        recs = [rec for _, rec, _ in reuse.records("gate")]
    return sorted(r["how"] for r in recs if r.get("sha") == sha and r.get("verdict") == "green")


def _git(repo: Path, *args: str) -> str:
    env = hermetic_env(drop_git=True)
    out = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                          *args], capture_output=True, text=True, env=env, check=True,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    return out.stdout.strip()


def _cli(store: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(REUSE), *args], capture_output=True, text=True,
                          env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), **env),
                          stdin=subprocess.DEVNULL, check=False, timeout=patience(60))  # fmt: skip


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    fast_init(repo, "main")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def test_the_gate_path_follows_the_tree_and_misses_a_changed_tree_or_gate(tmp_path):
    store = tmp_path / "store"
    repo = _repo(tmp_path)
    one = _git(repo, "rev-parse", "HEAD")
    r = _cli(store, "record-gate", "--repo", str(repo), one)
    assert r.returncode == 0, r.stderr
    hit = _cli(store, "lookup-gate", "--repo", str(repo), "--sha", one)
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["sha"] == one
    _git(repo, "commit", "-q", "--amend", "-m", "the same tree, another sha")
    same_tree = _git(repo, "rev-parse", "HEAD")
    assert _cli(store, "lookup-gate", "--repo", str(repo), "--sha", same_tree).returncode == 0
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "two")
    two = _git(repo, "rev-parse", "HEAD")
    miss = _cli(store, "lookup-gate", "--repo", str(repo), "--sha", two)
    assert miss.returncode == 1 and "no gate verdict" in miss.stderr, miss.stderr  # changed tree
    other_gate = tmp_path / "goh"
    other_gate.write_text("another goh binary\n")
    moved = _cli(store, "lookup-gate", "--repo", str(repo), "--sha", one, GOH_BIN=str(other_gate))
    assert moved.returncode == 1, moved.stdout  # changed gate identity
    release = _cli(store, "record-gate", "--repo", str(repo), f"{two}:release")
    assert release.returncode == 0, release.stderr
    assert _cli(store, "lookup-gate", "--repo", str(repo), "--sha", two).returncode == 1  # not full


def test_a_land_record_is_a_gate_verdict_under_its_identity_only(tmp_path):
    store = tmp_path / "store"
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    landed = repo / ".git" / "goh-landed"
    landed.write_text(f"{sha} {'0' * 64}\n")
    assert _cli(store, "lookup-gate", "--repo", str(repo), "--sha", sha).returncode == 1
    argv = [sys.executable, str(REPO_ROOT / "lib" / "gate_identity.py"), "key", "--mode", "full"]
    env = hermetic_env(drop_git=True)
    ident = subprocess.run(argv, capture_output=True, text=True, check=True,
                           stdin=subprocess.DEVNULL, env=env).stdout.strip()  # fmt: skip
    landed.write_text(f"{sha} {ident}\n")
    hit = _cli(store, "lookup-gate", "--repo", str(repo), "--sha", sha)
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["by"] == "goh-landed"


def test_paths_lists_every_registered_path_with_its_fields(tmp_path):
    out = _cli(tmp_path / "store", "paths")
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[0].startswith("gate\trepo,tree,identity\t")


def _pushed(store: Path, repo: Path, sha: str, **env: str) -> bool:
    code = "import sys; sys.path.insert(0, sys.argv[1]); import reuse, pathlib; " \
           "print(reuse.pushed(pathlib.Path(sys.argv[2]), sys.argv[3]))"  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code, str(REPO_ROOT / "lib"), str(repo), sha],
                         env=hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), **env),
                         capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
                         timeout=patience(60))  # fmt: skip
    return out.stdout.strip() == "True"


def test_the_pushed_path_is_the_tree_alone_under_any_gate(tmp_path):
    """The floor rule's base (lib/config_push.py): keyed WITHOUT the gate identity on purpose."""
    store = tmp_path / "store"
    repo = _repo(tmp_path)
    one = _git(repo, "rev-parse", "HEAD")
    assert not _pushed(store, repo, one)
    assert _cli(store, "record-gate", "--repo", str(repo), f"{one}:config").returncode == 0
    assert _pushed(store, repo, one)
    other_gate = tmp_path / "goh"
    other_gate.write_text("another goh binary\n")
    assert _pushed(store, repo, one, GOH_BIN=str(other_gate))  # another goh: still the base
    (repo / "a.txt").write_text("two\n")
    _git(repo, "commit", "-q", "-am", "two")
    two = _git(repo, "rev-parse", "HEAD")
    assert not _pushed(store, repo, two)  # a changed tree misses
    (repo / ".git" / "goh-pushed").write_text(f"{two} full\n")
    assert _pushed(store, repo, two)  # the retired file's base still counts, read-only
