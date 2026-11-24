"""A test red on main never reds an item; one the item turns red still does (GOH.MAINREDWAIVE).

lib/main_reading.py keeps readings of main (goh.db 0010), lib/land_waive.py turns those at an
item's base into GOH_LAND_WAIVE, and tests/_land_waive.py applies it in the suite. Each case is
two-way: the same planted red with and without the reading on main that waives it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import _goh_db_fixture as dbfix
from _waive_kit import commit, lane, main_repo, put, suite
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
import idle_suite
import land_reds
import land_waive
import main_reading
import main_red

SHARED = "tests/test_a.py::test_shared"
OK = "tests/test_a.py::test_ok"
FILES = {
    "src/value.txt": "1\n",
    "tests/test_a.py": (
        "from pathlib import Path\n"
        "def test_shared():\n    assert False, 'red on main'\n"
        "def test_ok():\n"
        "    assert (Path(__file__).parent.parent / 'src/value.txt').read_text() == '1\\n'\n"
    ),
}


def _planted(tmp_path: Path, monkeypatch, breaks_ok: bool = False):
    dbfix.made(monkeypatch, tmp_path)
    m, base = main_repo(tmp_path, FILES)
    wt = lane(m, base)
    put(wt, "src/value.txt", "2\n" if breaks_ok else "1\n")
    put(wt, "notes.txt", "the item's own change\n")
    return m, base, wt, commit(wt, "item")


def test_a_red_main_reading_at_the_base_waives_it_and_without_one_it_reds(tmp_path, monkeypatch):
    m, base, wt, tip = _planted(tmp_path, monkeypatch)
    req = {"repo": str(m), "sha": tip, "item": "GOH.PLANTED"}
    assert land_waive.words([req]) == []  # no reading on main: nothing is waivable
    red = suite(wt, [])
    assert red.returncode == 1, red.stdout
    rid = main_reading.record(m, base, "idle-suite", "*", [SHARED])
    waive = land_waive.words([req])
    run = suite(wt, waive)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 xfailed" in run.stdout and "1 passed" in run.stdout
    log = tmp_path / "gate.log"
    log.write_text(run.stdout)
    waived = land_reds.fields({"state": "landed", "log": str(log)})["waived"]
    assert [(w["node"], w["kind"], w["reading"], w["sha"]) for w in waived] == [
        (SHARED, "main-red", rid, base)
    ]


def test_a_test_the_item_turns_red_at_a_green_base_still_reds_it(tmp_path, monkeypatch):
    m, base, wt, tip = _planted(tmp_path, monkeypatch, breaks_ok=True)
    main_reading.record(m, base, "idle-suite", "*", [SHARED])  # main red on SHARED only
    run = suite(wt, land_waive.words([{"repo": str(m), "sha": tip}]))
    assert run.returncode == 1, run.stdout
    assert f"FAILED {OK}" in run.stdout and "1 xfailed" in run.stdout


def test_a_reading_at_another_sha_or_a_later_green_one_waives_nothing(tmp_path, monkeypatch):
    m, base, wt, tip = _planted(tmp_path, monkeypatch)
    put(m, "later.txt", "main moved\n")
    later = commit(m, "main moves")
    main_reading.record(m, later, "probe", [SHARED], [SHARED])  # red, but not at the item's base
    got = json.loads(land_waive.words([{"repo": str(m), "sha": tip}])[0].split("=", 1)[1])
    assert set(got["main"]) == {later}
    assert suite(wt, land_waive.words([{"repo": str(m), "sha": tip}])).returncode == 1
    main_reading.record(m, base, "idle-suite", "*", [SHARED])
    assert main_reading.red_at(m, base) == {SHARED: main_reading.red_at(m, base)[SHARED]}
    main_reading.record(m, base, "probe", [SHARED], [])  # a later run of it on main: green
    assert main_reading.red_at(m, base) == {}
    assert suite(wt, land_waive.words([{"repo": str(m), "sha": tip}])).returncode == 1


def test_the_waiver_reaches_xdist_workers(tmp_path, monkeypatch):
    m, base, wt, tip = _planted(tmp_path, monkeypatch)
    main_reading.record(m, base, "idle-suite", "*", [SHARED])
    run = suite(wt, land_waive.words([{"repo": str(m), "sha": tip}]), "-n", "2")
    assert run.returncode == 0, run.stdout + run.stderr
    assert land_waive.LINE in run.stdout


def test_a_probe_reads_main_on_a_clean_checkout_and_no_verdict_records_nothing(
    tmp_path, monkeypatch
):
    m, base, _wt, _tip = _planted(tmp_path, monkeypatch)
    seen = []

    def runner(wt: Path, nodes: list[str]) -> tuple[int, str]:
        seen.append(
            (git(wt, "rev-parse", "HEAD").strip(), nodes, (wt / "tests/test_a.py").is_file())
        )
        return 1, f"FAILED {SHARED} - AssertionError\n1 failed, 1 passed\n"

    q = tmp_path / "q"
    rid = main_reading.probe(q, m, base, [SHARED, OK], runner)
    assert seen == [(base, [SHARED, OK], True)]
    assert main_reading.red_at(m, base) == {SHARED: rid}
    assert not list((q / main_reading.DIR).iterdir())  # its checkout is gone
    for rc, out in ((124, "timed out"), (1, "ImportError while loading conftest")):
        assert main_reading.probe(q, m, base, [OK], lambda *_a, rc=rc, out=out: (rc, out)) is None
    assert main_reading.probe(q, m, base, [OK], lambda *_a: (0, "1 passed")) is not None
    assert main_reading.red_at(m, base) == {SHARED: rid}


def test_the_lander_probes_main_once_for_the_idle_suites_reds(tmp_path, monkeypatch):
    m, base, _wt, _tip = _planted(tmp_path, monkeypatch)
    q = tmp_path / "q"
    (q / "idle-suite").mkdir(parents=True)
    (q / "idle-suite" / "last.json").write_text(json.dumps({"sha": base, "outcome": "red"}))
    main_red.log_of(q, base).parent.mkdir(parents=True)
    main_red.log_of(q, base).write_text(f"FAILED {SHARED} - AssertionError\n")
    calls = []

    def runner(wt: Path, nodes: list[str]) -> tuple[int, str]:
        calls.append(nodes)
        return 1, f"FAILED {SHARED} - AssertionError\n"

    rid = main_reading.ensure(q, m, lambda _s: None, runner)
    assert calls == [[SHARED]] and main_reading.red_at(m, base) == {SHARED: rid}
    assert main_reading.ensure(q, m, lambda _s: None, runner) is None  # read already: no probe
    assert calls == [[SHARED]]


def test_the_idle_suite_keeps_its_run_as_a_reading_of_main(tmp_path, monkeypatch):
    m, base, _wt, _tip = _planted(tmp_path, monkeypatch)
    log = tmp_path / "idle.log"
    log.write_text(f"FAILED {SHARED} - AssertionError\n1 failed\n")
    said = idle_suite.reading(m, base, "red", log)
    assert "reading" in said and main_reading.red_at(m, base) == {SHARED: int(said.split()[6])}
    log.write_text("collection error, no test named\n")
    assert "no reading kept" in idle_suite.reading(m, base, "red", log)
    idle_suite.reading(m, base, "green", log)
    assert main_reading.red_at(m, base) == {}


def test_no_goh_db_is_no_reading(tmp_path, monkeypatch):
    dbfix.use(monkeypatch, tmp_path)
    assert main_reading.red_at(tmp_path, "0" * 40) == {}
    assert not main_reading.covered(tmp_path, "0" * 40, [SHARED])
    assert not (tmp_path / "goh.db").exists()
    assert land_waive.words([{"repo": str(tmp_path), "sha": "0" * 40}]) == []


def test_land_check_waives_main_red_guards_by_a_reading(tmp_path, monkeypatch):
    from lib import land_check

    dbfix.made(monkeypatch, tmp_path)
    m, base = main_repo(
        tmp_path, {**FILES, "tests/test_a.py": "# goh-land-guard:\n" + FILES["tests/test_a.py"]}
    )
    wt = lane(m, base)
    put(wt, "notes.txt", "the item's own change\n")
    commit(wt, "item")
    said: list[str] = []
    assert land_check.run(wt, None, said.append, str(m)) != []
    main_reading.record(m, base, "idle-suite", "*", [SHARED])
    assert land_check.run(wt, None, said.append, str(m)) == [], said


def test_the_lander_reads_main_before_a_lone_land_and_a_batch(tmp_path, monkeypatch):
    import landq_batch
    import landq_size
    from test_landq_size import pend

    seen: list[str] = []
    monkeypatch.setattr(main_reading, "ensure", lambda _q, repo, _say: seen.append(repo))
    path = pend(tmp_path, 1)[0]
    got = landq_batch.run(tmp_path, [path], None, lambda _q, _p: {"log": "", "state": "landed"},
                          landq_size.Size(1, "why"))  # fmt: skip
    assert got[0]["state"] == "landed" and seen == [landq_batch.read(path)["repo"]]
