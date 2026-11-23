"""GOH.CAPAHEAD: a land that grows a file into its cap's band files the file's split item.

A fixture loop queue (a fake `loopctl file` that keeps the items and refuses an id it holds) and a
fixture repo whose cap is 100 (band over 50): the land's own files that cross 50 are filed, with
their responsibilities named; a file already over, a file still under, and a file ANOTHER land on
main grew are not; a red land and a repo with no cap file nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from _tree_clone import from_template
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cap_ahead

FAKE_LOOPCTL = """\
import json, os, sys
q = os.environ["FAKE_QUEUE"]
item = json.loads(sys.argv[sys.argv.index("--json") + 1])
items = json.load(open(q)) if os.path.exists(q) else []
if any(i["id"] == item["id"] for i in items):
    print(f"{item['id']} already exists"); sys.exit(1)
items.append(item)
json.dump(items, open(q, "w"))
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                           "-c", "commit.gpgsign=false", *args],
                          check=True, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip


def _write(repo: Path, rel: str, lines: int, name: str = "f") -> None:
    text = "".join(f"def {name}{i}():\n" if i % 2 == 0 else "    pass\n" for i in range(lines))
    (repo / rel).write_text(text)


def _commit(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _build_tree(repo: Path, gatesrc: str) -> None:
    fast_init(repo)
    (repo / ".gatesrc").write_text(gatesrc)
    _write(repo, "grow.py", 40, "grow")
    _write(repo, "big.py", 60)
    _write(repo, "small.py", 10)
    _write(repo, "other.py", 40)
    (repo / "notes.md").write_text("x\n" * 40)
    _commit(repo, "base")


def _build_capped(repo: Path) -> None:
    _build_tree(repo, "GOH_MAX_LINES=100\n")


def _build_uncapped(repo: Path) -> None:
    _build_tree(repo, "# no cap\n")


def _fixture(factory, tmp_path: Path, build=_build_capped) -> tuple[Path, str]:
    """The base repo, built once per run and cloned (tests/_tree_clone.py)."""
    repo = from_template(factory, f"cap-ahead-{build.__name__}", build, tmp_path / "repo")
    return repo, _git(repo, "rev-parse", "HEAD")


def _lane(repo: Path) -> str:
    _write(repo, "grow.py", 55, "grow")
    _commit(repo, "lane 1")
    _write(repo, "big.py", 70)
    _write(repo, "small.py", 20)
    _write(repo, "new.py", 51, "new")
    (repo / "notes.md").write_text("x\n" * 90)  # not a measured source
    return _commit(repo, "lane 2")


def _run(tmp_path, monkeypatch, repo, landed, enqueued, base, state="landed"):
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("FAKE_QUEUE", str(tmp_path / "queue.json"))
    monkeypatch.delenv("GOH_LOOP_QUEUE", raising=False)
    outcome = {"state": state, "landed": landed, "sha": landed, "repo": str(repo)}
    req = {"sha": enqueued, "pre": {"judged": enqueued, "base": base}}
    return cap_ahead.file_items(outcome, req, str(fake), "GOH.LANE")


def _queue(tmp_path: Path) -> list[dict]:
    path = tmp_path / "queue.json"
    return json.loads(path.read_text()) if path.exists() else []


def test_a_fast_forward_land_files_each_file_it_took_into_the_band(
    tmp_path, tmp_path_factory, monkeypatch
):
    repo, base = _fixture(tmp_path_factory, tmp_path)
    tip = _lane(repo)
    got = _run(tmp_path, monkeypatch, repo, tip, tip, base)
    assert [(c["file"], c["id"], c["said"]) for c in got["cap_items"]] == [
        ("grow.py", "GOH.SPLITGROW", "filed"),
        ("new.py", "GOH.SPLITNEW", "filed"),
    ], got
    items = {i["id"]: i for i in _queue(tmp_path)}
    grow = items["GOH.SPLITGROW"]
    assert grow["status"] == "todo" and grow["phase"] == "HY" and grow["class_of"] == "none"
    assert "Split grow.py: 55 lines against its cap of 100" in grow["item"]
    assert "grow0, grow2, grow4" in grow["item"], "its responsibilities are named"
    # landing it again (a re-run of the record) files nothing new
    again = _run(tmp_path, monkeypatch, repo, tip, tip, base)
    assert {c["said"] for c in again["cap_items"]} == {"already filed"}
    assert len(_queue(tmp_path)) == 2


def test_a_rebased_land_files_only_its_own_files(tmp_path, tmp_path_factory, monkeypatch):
    repo, base = _fixture(tmp_path_factory, tmp_path)
    enq = _lane(repo)
    _git(repo, "checkout", "-q", "-b", "main2", base)
    _write(repo, "other.py", 60)  # ANOTHER land on main grew this one
    _commit(repo, "another land")
    _git(repo, "cherry-pick", f"{base}..{enq}")
    landed = _git(repo, "rev-parse", "HEAD")
    got = _run(tmp_path, monkeypatch, repo, landed, enq, base)
    assert [c["file"] for c in got["cap_items"]] == ["grow.py", "new.py"], got


def test_a_red_land_or_a_repo_with_no_cap_files_nothing(tmp_path, tmp_path_factory, monkeypatch):
    repo, base = _fixture(tmp_path_factory, tmp_path)
    tip = _lane(repo)
    assert _run(tmp_path, monkeypatch, repo, tip, tip, base, state="red") == {}
    nocap = tmp_path / "nocap"
    nocap.mkdir()
    repo2, base2 = _fixture(tmp_path_factory, nocap, _build_uncapped)
    tip2 = _lane(repo2)
    assert _run(tmp_path, monkeypatch, repo2, tip2, tip2, base2) == {}
    assert _queue(tmp_path) == []


def test_the_sweep_files_todays_files_within_25_lines_of_the_cap(
    tmp_path, tmp_path_factory, monkeypatch
):
    repo, _ = _fixture(tmp_path_factory, tmp_path)
    _write(repo, "near.py", 80)
    _write(repo, "at.py", 100)
    _commit(repo, "near")
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("FAKE_QUEUE", str(tmp_path / "queue.json"))
    said = cap_ahead.sweep(repo, str(fake))
    assert sorted(s.split(":")[0] for s in said) == ["at.py (100)", "near.py (80)"], said
    assert {i["id"] for i in _queue(tmp_path)} == {"GOH.SPLITAT", "GOH.SPLITNEAR"}


def test_the_lander_files_through_land_filers(tmp_path, tmp_path_factory, monkeypatch):
    """tools/landq.py's one filing call reaches the cap filer (the wiring, not only the module)."""
    import land_filers

    assert "land_filers.file_all(" in (REPO_ROOT / "tools" / "landq.py").read_text()
    repo, base = _fixture(tmp_path_factory, tmp_path)
    tip = _lane(repo)
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    monkeypatch.setenv("FAKE_QUEUE", str(tmp_path / "queue.json"))
    outcome = {"state": "landed", "landed": tip, "sha": tip, "repo": str(repo)}
    req = {"sha": tip, "pre": {"base": base}}
    got = land_filers.file_all(outcome, req, str(fake), "GOH.LANE")
    assert [c["id"] for c in got["cap_items"]] == ["GOH.SPLITGROW", "GOH.SPLITNEW"], got


def test_a_generic_file_name_carries_its_directory():
    assert cap_ahead.item_id("crates/goh/src/movingtag/tests.rs") == "GOH.SPLITMOVINGTAGTESTS"
    assert cap_ahead.item_id("tools/landq.py", "LOOP.X") == "LOOP.SPLITLANDQ"
