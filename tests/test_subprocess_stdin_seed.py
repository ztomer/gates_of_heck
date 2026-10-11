"""`goh subprocess-stdin --seed`: the allowlist a repo adopts the stdin gate with (BACKLOG 3.2).

The CLI tier beside the Rust unit table: a seed makes a red tree pass with its debt counted,
keeps a reviewed entry as written, drops what no longer matches, refuses over uncommitted Python,
shards past one file's worth, and the gate still goes red when a new call arrives after the seed.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import commit_all, run_goh, write

BAD = "import subprocess\nsubprocess.run(['ls'])\n"
BAD2 = "import subprocess\nsubprocess.run(['ls'])\nsubprocess.run(['pwd'])\n"


def _entries(repo: Path) -> list[dict]:
    files = [repo / "subprocess_stdin_allow.json"]
    files += sorted((repo / "subprocess_stdin_allow.d").glob("*.json"))
    out: list[dict] = []
    for f in files:
        if f.is_file():
            out += json.loads(f.read_text())["entries"]
    return out


def _gate(repo: Path):
    return run_goh(repo, "subprocess-stdin", "--exclude", "")


def _seed(repo: Path):
    return run_goh(repo, "subprocess-stdin", "--exclude", "", "--seed")


def test_a_seed_turns_a_red_tree_green_and_counts_the_debt(repo: Path) -> None:
    write(repo, "a.py", BAD2)
    commit_all(repo)
    assert _gate(repo).returncode == 1
    r = _seed(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 new and 'unreviewed'" in r.stdout, r.stdout
    assert [e["status"] for e in _entries(repo)] == ["unreviewed", "unreviewed"]
    commit_all(repo)
    g = _gate(repo)
    assert g.returncode == 0, g.stdout + g.stderr
    assert "2 'unreviewed'" in g.stdout, g.stdout


def test_a_new_call_after_the_seed_is_red(repo: Path) -> None:
    write(repo, "a.py", BAD)
    commit_all(repo)
    assert _seed(repo).returncode == 0
    write(repo, "a.py", BAD2)
    commit_all(repo)
    g = _gate(repo)
    assert g.returncode == 1, g.stdout
    assert "pwd" in g.stdout, g.stdout


def test_a_reseed_keeps_a_reviewed_entry_and_drops_a_fixed_one(repo: Path) -> None:
    write(repo, "a.py", BAD2)
    commit_all(repo)
    assert _seed(repo).returncode == 0
    allow = repo / "subprocess_stdin_allow.json"
    data = json.loads(allow.read_text())
    data["entries"][0].update(status="legitimate", reason="the child reads its stdin on purpose")
    allow.write_text(json.dumps(data))
    write(repo, "a.py", BAD)  # the second call is fixed
    commit_all(repo)
    r = _seed(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 kept as written" in r.stdout and "1 dropped as stale" in r.stdout, r.stdout
    [only] = _entries(repo)
    assert only["status"] == "legitimate"
    assert only["reason"] == "the child reads its stdin on purpose"


def test_a_seed_refuses_over_uncommitted_python(repo: Path) -> None:
    write(repo, "a.py", BAD)
    commit_all(repo)
    write(repo, "a.py", BAD2)
    r = _seed(repo)
    assert r.returncode == 1, r.stdout
    assert "uncommitted Python" in r.stdout and "a.py" in r.stdout, r.stdout
    assert not (repo / "subprocess_stdin_allow.json").exists()


def test_past_one_files_worth_the_seed_shards_under_the_line_cap(repo: Path) -> None:
    calls = "".join(f"subprocess.run(['echo', '{i}'])\n" for i in range(450))
    write(repo, "many.py", "import subprocess\n" + calls)
    write(repo, "subprocess_stdin_allow.json", '{"entries": []}\n')
    commit_all(repo)
    r = _seed(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    shards = sorted((repo / "subprocess_stdin_allow.d").glob("*.json"))
    assert len(shards) == 2, shards
    assert all(len(s.read_text().splitlines()) < 500 for s in shards)
    assert not (repo / "subprocess_stdin_allow.json").exists()
    assert len(_entries(repo)) == 450
    commit_all(repo)
    assert _gate(repo).returncode == 0
