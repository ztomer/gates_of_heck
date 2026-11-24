"""A rule that landed after an item's base warns that item; one at its base reds it, and so does
the item's next land (GOH.RULEINFLIGHT).

The planted rule is a tree-wide judge main gains after the lane forked: `test_no_bad_files` walks
the tree for a marker the lane's own file carries. lib/land_waive.py finds it (in main's tree,
absent from every enqueued tree), tests/_land_waive.py makes its red a warning naming the rule.
"""

from __future__ import annotations

import json
import sys

import _goh_db_fixture as dbfix
from _waive_kit import commit, lane, main_repo, put, suite
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_reds
import land_waive

RULE = "tests/test_rule.py::test_no_bad_files"
RULE_TEXT = (
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parent.parent\n"
    "def test_no_bad_files():\n"
    "    bad = [p.name for p in ROOT.rglob('*.txt') if 'BAD' in p.read_text()]\n"
    "    assert not bad, bad\n"
)
PLAIN_TEXT = (  # a new test of one file: no rule, so its red is the item's
    "from pathlib import Path\n"
    "def test_plain():\n"
    "    assert 'BAD' not in (Path(__file__).parent.parent / 'src/bad.txt').read_text()\n"
)
FILES = {"src/a.txt": "fine\n", "tests/test_base.py": "def test_ok():\n    assert True\n"}


def _in_flight(tmp_path, monkeypatch, rule: str = RULE_TEXT, name: str = "tests/test_rule.py"):
    """A lane forked before main gained RULE, its tip violating it, then rebased onto main the way
    the lander gates it; (main, the lane, its enqueued sha)."""
    dbfix.made(monkeypatch, tmp_path)
    m, base = main_repo(tmp_path, FILES)
    wt = lane(m, base)
    put(wt, "src/bad.txt", "BAD\n")
    enqueued = commit(wt, "item")
    put(m, name, rule)
    commit(m, "main gains a test")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "rebase", "-q", "main")
    return m, wt, enqueued


def test_a_rule_newer_than_the_item_warns_it_naming_the_rule(tmp_path, monkeypatch):
    m, wt, enqueued = _in_flight(tmp_path, monkeypatch)
    assert suite(wt, []).returncode == 1  # without the waiver: the rule reds the item
    waive = land_waive.env([{"repo": str(m), "sha": enqueued, "item": "GOH.PLANTED"}])
    got = json.loads(waive[0].split("=", 1)[1])
    assert list(got["rules"]) == [RULE] and "main gains a test" in got["rules"][RULE]["commit"]
    run = suite(wt, waive)
    assert run.returncode == 0, run.stdout + run.stderr
    log = tmp_path / "gate.log"
    log.write_text(run.stdout)
    (w,) = land_reds.fields({"state": "landed", "log": str(log)})["waived"]
    assert (w["kind"], w["rule"], w["items"]) == ("rule-in-flight", RULE, ["GOH.PLANTED"])
    assert "still owes the fix" in w["reason"] and "main gains a test" in w["reason"]


def test_a_rule_at_the_items_base_reds_it(tmp_path, monkeypatch):
    dbfix.made(monkeypatch, tmp_path)
    m, _ = main_repo(tmp_path, {**FILES, "tests/test_rule.py": RULE_TEXT})
    wt = lane(m, git(m, "rev-parse", "HEAD").strip())
    put(wt, "src/bad.txt", "BAD\n")
    enqueued = commit(wt, "item")
    assert land_waive.env([{"repo": str(m), "sha": enqueued, "item": "GOH.PLANTED"}]) == []
    assert suite(wt, []).returncode == 1


def test_the_warning_is_red_on_the_items_next_land(tmp_path, monkeypatch):
    m, wt, enqueued = _in_flight(tmp_path, monkeypatch)
    req = {"repo": str(m), "sha": enqueued, "item": "GOH.PLANTED"}
    warned = {"node": RULE, "kind": "rule-in-flight", "rule": RULE}
    dbfix.land(tmp_path / "q", "01-GOH.PLANTED", {"item": "GOH.PLANTED", "state": "red",
                                                  "waived": [warned]})  # fmt: skip
    assert land_waive.env([req]) != []  # a red land spent no grace
    dbfix.land(tmp_path / "q", "02-GOH.PLANTED", {"item": "GOH.PLANTED", "state": "landed",
                                                  "waived": [warned]})  # fmt: skip
    assert land_waive.env([req]) == []
    assert suite(wt, land_waive.env([req])).returncode == 1
    assert land_waive.env([{**req, "item": "GOH.OTHER"}]) != []  # another item's grace is its own


def test_a_new_test_that_is_no_rule_reds_the_item(tmp_path, monkeypatch):
    m, wt, enqueued = _in_flight(tmp_path, monkeypatch, PLAIN_TEXT, "tests/test_plain.py")
    assert land_waive.env([{"repo": str(m), "sha": enqueued, "item": "GOH.PLANTED"}]) == []
    assert suite(wt, []).returncode == 1


def test_a_stack_waives_a_rule_only_when_every_item_predates_it(tmp_path, monkeypatch):
    m, _wt, enqueued = _in_flight(tmp_path, monkeypatch)
    after = lane(m, git(m, "rev-parse", "HEAD").strip(), "after")
    put(after, "src/other.txt", "ok\n")
    later = commit(after, "an item based after the rule")
    both = [{"repo": str(m), "sha": enqueued}, {"repo": str(m), "sha": later}]
    assert land_waive.env(both) == []
    assert land_waive.rules_in_flight(str(m), git(m, "rev-parse", "HEAD").strip(), [enqueued])
