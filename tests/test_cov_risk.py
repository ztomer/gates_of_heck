"""The land's diff coverage warning (lib/cov_risk.py, GOH.DIFFCOVLAND).

Modelled on v0.142.0 (ship record 20261121T213942Z-2f3c0528): the new crates/goh/src/toolshell/
arms ran only under the external suite or not at all, and the ship's diff coverage found 46 of 345
lines red that no land had seen. Here the last ship's parts are folded into a kept profile, the
land changes more, and the warning names exactly the lines no in-process test ran, with no
instrumented build (its wall time is printed by `goh land --check`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT))
from lib import cov_risk


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip


def _write(repo: Path, rel: str, n: int, tag: str = "x") -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"let {tag}{i} = {i};\n" for i in range(1, n + 1)))


def _part(path: Path, records: dict[str, dict[int, int]]) -> None:
    out = []
    for sf, das in records.items():
        out.append(f"SF:{sf}")
        out += [f"DA:{ln},{c}" for ln, c in sorted(das.items())]
        out.append("end_of_record")
    path.write_text("\n".join(out) + "\n")


SPLIT = "crates/goh/src/toolshell/split.rs"
CHECK = "crates/goh/src/toolshell/check.rs"
MOD = "crates/goh/src/toolshell/mod.rs"
LIB = "crates/goh/src/lib.rs"
CLI = "crates/goh/src/cli.rs"  # in the measured commit, excluded from its report
TESTS_RS = "crates/goh/src/toolshell/tests.rs"


def _shipped(tmp_path: Path, monkeypatch) -> Path:
    """A repo whose last ship (v0.1.0, then a red ship's commit) left a kept profile."""
    repo = tmp_path / "gates_of_heck"
    fast_init(repo, "main")
    _write(repo, LIB, 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "tag", "v0.1.0")
    _write(repo, SPLIT, 20)
    _write(repo, CHECK, 10)
    _write(repo, CLI, 5)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "toolshell")  # the ship's measured commit
    parts = tmp_path / "parts"
    parts.mkdir()
    ws = {
        str(repo / LIB): dict.fromkeys(range(1, 21), 1),
        str(repo / SPLIT): {**dict.fromkeys(range(1, 10), 2), 10: 0, 11: 0, 12: 0, 13: 0},
    }
    ext = {
        str(repo / SPLIT): {10: 4, 11: 4, 12: 4, 13: 0},
        str(repo / CHECK): dict.fromkeys((2, 3, 4), 1),
    }
    _part(parts / "part-workspace.info", ws)
    _part(parts / "part-external.info", ext)
    ships = tmp_path / "ship"
    monkeypatch.setenv("GOH_SHIP_DIR", str(ships))
    assert cov_risk.keep(parts, repo, ships / "gates_of_heck" / cov_risk.NAME)
    # The land after the red ship: lib.rs edited where in-process tests run, mod.rs new.
    text = (repo / LIB).read_text().replace("let x5 = 5;", "let x5 = 50;")
    (repo / LIB).write_text(text)
    _write(repo, MOD, 3, "m")
    _write(repo, TESTS_RS, 4, "t")
    (repo / CLI).write_text("changed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "more")
    return repo


def test_the_warning_names_the_toolshell_lines_no_in_process_test_ran(tmp_path, monkeypatch):
    repo = _shipped(tmp_path, monkeypatch)
    risky, said = cov_risk.at_risk(repo, cov_risk.summary_path(repo))
    got = {f: (ls, why) for f, ls, why in risky}
    assert got[SPLIT][0] == [10, 11, 12, 13], said  # external only (10-12) and no test (13)
    assert got[CHECK][0] == [2, 3, 4]  # the external suite alone
    assert got[MOD] == ([1, 2, 3], "new since the last ship's profile")
    assert CLI not in got and TESTS_RS not in got  # never measured, so never judged
    assert LIB not in got  # its edit sits among in-process-covered lines
    lines, rec = cov_risk.report(repo, repo)
    assert any(f"{SPLIT}:10-13" in ln for ln in lines), lines
    assert rec["lines"] == 10 and rec["files"][CHECK] == "2-4"
    json.dumps(rec)  # it goes on the land record


def test_no_profile_is_said_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_SHIP_DIR", str(tmp_path / "none"))
    seen: list[str] = []
    rec = cov_risk.say(tmp_path, tmp_path / "repo", seen.append)
    assert "no kept ship coverage profile" in seen[0] and rec["lines"] == 0


def test_a_run_whose_part_never_finished_is_not_kept_as_the_profile(tmp_path, monkeypatch):
    """2026-11-23: the ship of 9c27e293, its external suite red, was kept as the profile; the next
    lands carried 482 lines as uncovered and the next complete run put them at 3."""
    monkeypatch.setattr(cov_risk, "_git", lambda top, *a: "c" * 40)
    parts = tmp_path / cov_risk.PARTS
    parts.mkdir(parents=True)
    dest = tmp_path / "ship" / cov_risk.NAME
    _part(parts / "part-workspace.info", {str(tmp_path / LIB): {1: 1}})
    for name in ("part-workspace.info.log", "part-workspace.info.ok", "part-external.info.log"):
        (parts / name).touch()
    assert "no complete lcov parts" in cov_risk.keep_run(tmp_path, dest) and not dest.exists()
    (parts / "part-external.info.ok").touch()
    assert cov_risk.keep_run(tmp_path, dest) == "" and dest.exists()
    cov_risk.fresh(tmp_path)  # the next run's start: one that measures nothing keeps nothing
    dest.unlink()
    assert "no complete lcov parts" in cov_risk.keep_run(tmp_path, dest) and not dest.exists()


def test_the_land_record_keeps_the_warning_landed_or_red():
    from lib import land_check

    req = {"cov_risk": {"lines": 3, "files": {SPLIT: "10-12"}}}
    assert land_check.fields({"state": "landed"}, req) == {"cov_risk": req["cov_risk"]}
    red = land_check.fields({"state": "red", "why": "gate exit 1", "log": ""}, req)
    assert red["cov_risk"] == req["cov_risk"] and red["guard"] == "gate exit 1"
