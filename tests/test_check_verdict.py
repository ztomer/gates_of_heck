"""A green `goh land --check` is kept and taken by the enqueue, and only by one it answers for
(lib/check_verdict.py, lib/land_check.py, GOH.CHECKVERDICT); what the check refuses fails closed.

Each rule of the key is planted broken here: another tree, base, gate code or environment, a
checkout that is not its commit, a run with a waiver, a job the kept run did not run.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import reuse
import reuse_audit
import reuse_store

from lib import (
    check_jobs,
    check_verdict,
    commit_stamp,
    fragment_check,
    land_check,
    land_judges,
    landq_early,
)

FIELDS = dict.fromkeys(reuse.PATHS["check"].fields, "x")
STEPS, NODES = ["./gates/structural.sh"], ["tests/test_a.py::test_every_a"]


@pytest.fixture
def store(monkeypatch):
    """The one store and its audit, in memory."""
    kept: dict[tuple[str, str], dict] = {}
    monkeypatch.setattr(reuse_store, "put", lambda p, k, r: kept.update({(p, k): r}) or k)
    monkeypatch.setattr(reuse_store, "get", lambda p, k: (kept.get((p, k)), k, ""))
    monkeypatch.setattr(reuse, "prune", lambda p: 0)
    monkeypatch.setattr(reuse_audit, "use", lambda p, f: "reuse")
    return kept


def hold(monkeypatch, key=FIELDS, door="check", waived=False, root=Path("/nowhere")):
    monkeypatch.setattr(check_verdict, "key", lambda root, env: key and dict(key))
    return check_verdict.Hold(root, {}, door, waived)


def keep(monkeypatch, refused=(), **kw) -> None:
    hold(monkeypatch, **kw).close(STEPS, NODES, list(refused), False, lambda: True)


def taken(monkeypatch, steps=STEPS, nodes=NODES, **kw) -> bool:
    return hold(monkeypatch, door="enqueue", **kw).take(steps, nodes, lambda s: None)


def test_a_green_check_is_taken_for_its_own_key_whole_or_not_at_all(store, monkeypatch):
    assert not taken(monkeypatch), "nothing is kept yet"
    keep(monkeypatch)
    assert taken(monkeypatch)
    assert not taken(monkeypatch, steps=[*STEPS, "./gates/rust_gate.sh"]), "a --no-pre's lint"
    assert not taken(monkeypatch, nodes=[*NODES, "tests/test_b.py::test_no_b"]), "a guard not run"
    assert taken(monkeypatch, nodes=[])  # judged more than is asked
    for f in FIELDS:
        assert not taken(monkeypatch, key={**FIELDS, f: "another"}), f
    assert not taken(monkeypatch, waived=True)
    assert not taken(monkeypatch, key=None)
    (rec,) = store.values()
    rec["verdict"] = "red"
    assert not taken(monkeypatch), "only a green is taken"
    rec["verdict"] = "green"
    monkeypatch.setenv("GOH_REUSE_NOW", str(reuse.now() + reuse.PATHS["check"].ttl_s + 60))
    assert not taken(monkeypatch), "past its life"


def test_only_a_wholly_green_run_of_an_unmoved_checkout_is_kept(store, monkeypatch, tmp_path):
    keep(monkeypatch, refused=["tests/test_a.py::test_every_a"])
    keep(monkeypatch, waived=True)
    keep(monkeypatch, key=None)
    assert store == {}
    moved = hold(monkeypatch)  # main, the tree or the gates moved while it ran
    monkeypatch.setattr(check_verdict, "key", lambda root, env: {**FIELDS, "base": "moved"})
    moved.close(STEPS, NODES, [], False, lambda: True)
    edited = hold(monkeypatch)  # edited and put back while it ran
    monkeypatch.setattr(check_verdict, "touched", lambda root, since: "lib/x.py")
    edited.close(STEPS, NODES, [], False, lambda: True)
    assert store == {}


def test_a_file_written_back_as_it_was_is_seen(repo):
    since = (repo / "README.md").lstat().st_ctime_ns + 1
    assert check_verdict.touched(repo, since) == ""
    readme = repo / "README.md"
    readme.write_text(readme.read_text())
    assert check_verdict.touched(repo, since) == "README.md"


def test_a_shadowed_take_runs_the_guards_and_is_compared(store, monkeypatch):
    keep(monkeypatch)
    seen: list[tuple] = []
    monkeypatch.setattr(reuse_audit, "use", lambda p, f: "shadow")
    monkeypatch.setattr(
        reuse_audit,
        "compare",
        lambda p, f, reused, fresh, again: seen.append((p, reused, fresh, again())),
    )
    for refused, blind, again in ((["x"], False, False), ([], False, True), (["x"], True, False)):
        h = hold(monkeypatch, door="enqueue")
        assert not h.take(STEPS, NODES, lambda s: None)
        h.close(STEPS, NODES, refused, blind, lambda a=again: a)
    assert seen == [
        ("check", "green", "red", ("green", "red")),  # repeated: the audit turns the path off
        ("check", "green", "green", ("green", "green")),
    ], "a blind run judged nothing, so it is no sample"
    monkeypatch.setattr(reuse_audit, "use", lambda p, f: "off")
    h = hold(monkeypatch, door="enqueue")
    assert not h.take(STEPS, NODES, lambda s: None) and not h.shadow


def test_the_key_moves_with_the_tree_the_base_the_gate_code_and_the_environment(repo, monkeypatch):
    code = ["goh content 1"]
    monkeypatch.setattr(check_verdict.gate_identity, "proven", lambda *a: list(code))
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    git(repo, "switch", "-q", "-c", "lane")
    env = {"GOH_PY": sys.executable, "GOH_X": "1", "HOME": "/h", "GOH_LOOP_AGENT": "a"}
    was = check_verdict.key(repo, env)
    env.update(HOME="/i", GOH_LOOP_AGENT="b")

    def moved() -> str:
        now = check_verdict.key(repo, env)
        return " ".join(f for f in FIELDS if now[f] != was[f])

    assert moved() == ""
    env["GOH_X"] = "2"
    assert moved() == "env"
    env["GOH_X"] = "1"
    code.append("tool ruff other")
    assert moved() == "identity"
    code.pop()
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "--no-verify", "-m", "same tree, another commit")  # fmt: skip
    assert moved() == "", "the tree, never the sha"
    git(repo, "update-ref", "refs/heads/main", "HEAD")
    assert moved() == "base"
    was = check_verdict.key(repo, env)
    git(repo, "switch", "-q", "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "--no-verify", "-m", "main moves on, the lane's fork does not")  # fmt: skip
    git(repo, "switch", "-q", "lane")
    assert moved() == "base"
    was = check_verdict.key(repo, env)
    (repo / "stray.txt").write_text("untracked\n")
    assert check_verdict.key(repo, env) is None
    git(repo, "add", "stray.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "t")
    assert moved() == "tree"
    git(repo, "branch", "-m", "main", "trunk")
    assert check_verdict.key(repo, env) is None, "no main: no base"


# THE DOORS (lib/land_check.py run). The enqueue takes; `--check` always judges; a batch's ratchet
# phase neither takes nor keeps.
def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    return tree


class FakeHold:
    made: ClassVar[list] = []

    def __init__(self, root, env, door, waived):
        self.door, self.closed = door, None
        FakeHold.made.append(self)

    def take(self, steps, nodes, say):
        return True

    def close(self, steps, nodes, refused, blind, again):
        self.closed = (steps, refused, blind)


def test_the_enqueue_takes_the_check_judges_and_a_batch_does_neither(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    monkeypatch.setenv("GOH_LAND_CHECK_STEPS", "exit 1")
    monkeypatch.setattr(check_verdict, "Hold", FakeHold)
    monkeypatch.setattr(FakeHold, "made", [])
    assert land_check.run(tree, None, lambda s: None, door="enqueue") == []
    assert FakeHold.made[0].closed is None, "a taken verdict is not kept as a new run"
    assert land_check.run(tree, None, lambda s: None, door="check") == ["exit 1"]
    assert FakeHold.made[1].closed == (["exit 1"], ["exit 1"], False)
    assert land_check.run(tree, None, lambda s: None) == ["exit 1"]
    assert len(FakeHold.made) == 2


# THE SELECTOR (lib/land_judges.py). A failed or timed-out selector answered [], and the check went
# green on no judge at all.
@pytest.mark.load_sensitive
def test_a_failed_selector_is_a_refusal_never_an_empty_green(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    (tree / "tools").mkdir()
    (tree / "tools" / "affected_tests.py").write_text(
        "import sys\nprint('tests/test_a.py')\nsys.exit('affected_tests: no base main here')\n"
    )
    monkeypatch.setenv("GOH_PY", sys.executable)
    monkeypatch.setenv("GOH_LAND_CHECK_STEPS", "true")
    with pytest.raises(land_judges.Unchosen, match="no base main here"):
        land_judges.chosen_by_diff(tree)
    said: list[str] = []
    (refused,) = land_check.run(tree, None, said.append, tables=True, cov=False)
    assert land_check.blind(refused) and "no base main here" in refused, refused
    assert any("true: green" in s for s in said), "the other jobs still ran to their verdicts"
    monkeypatch.setattr(land_judges, "SELECT_TIMEOUT", 1)
    monkeypatch.setattr(check_jobs.proc_deadline, "stretch", lambda: 1.0)
    (tree / "tools" / "affected_tests.py").write_text("import time\ntime.sleep(60)\n")
    with pytest.raises(land_judges.Unchosen, match="exit 124"):
        land_judges.chosen_by_diff(tree)
    (tree / "tools" / "affected_tests.py").unlink()
    assert land_judges.chosen_by_diff(tree) == [], "a tree with no selector chooses none"


# WHAT A RUN SAYS. GOH.PYSLOWTESTS lost an hour to "exit 4" with no name (2026-11-25).
def test_a_collection_error_names_the_import_error_it_printed(tmp_path, monkeypatch):
    tree = _tree(tmp_path)
    (tree / "tests" / "test_broken.py").write_text(
        "# goh-land-guard: test_x\nfrom os import no_such_name\ndef test_x(): pass\n"
    )
    monkeypatch.setenv("GOH_PY", sys.executable)
    said: list[str] = []
    (refused,) = land_check.run(tree, None, said.append)
    assert land_check.blind(refused) and "exit 4" in refused, refused
    assert "ImportError: cannot import name 'no_such_name'" in refused, refused
    assert "tests/test_broken.py" in refused, refused
    assert any("NO VERDICT" in s and "no_such_name" in s for s in said), said
    gone = "ERROR: not found: /x/tests/test_a.py::test_gone\n(no match in any of [])\n"
    assert land_check.named_stop(gone) == "not found: /x/tests/test_a.py::test_gone"


def test_a_failed_line_with_no_node_is_read():
    log = "ERROR tests/test_a.py\nFAILED tests/test_b.py - x\nFAILED tests/test_c.py::test_d[1]\n"
    assert land_check.guards(log) == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py::test_d",
    ]


def test_an_unmeasured_test_is_costed_as_one_not_as_nothing(monkeypatch):
    monkeypatch.setattr(land_judges, "costs", lambda: {"t.py::dear": 2.5})
    got = land_check.shards(["t.py::dear", *(f"t.py::new{i}" for i in range(4))], 2)
    assert got == [["t.py::dear", "t.py::new3"], ["t.py::new0", "t.py::new1", "t.py::new2"]]
    monkeypatch.setattr(land_judges, "costs", lambda: {"t.py::cheap": 0.5})
    got = land_check.shards(["t.py::cheap", "t.py::new0", "t.py::new1"], 2)
    assert got == [["t.py::cheap", "t.py::new0"], ["t.py::new1"]], "the costliest first"


# THE ENQUEUE reads the commits too: one no commit gate judged, an item with no fragment.
def test_the_enqueue_refuses_an_ungated_commit_and_a_missing_fragment(tmp_path, monkeypatch):
    tree, settled = _tree(tmp_path), []
    said: list[str] = []
    ran: list[str] = []
    ungated = "the commit gate on 0123456789 (one), which no commit gate judged:\nfound X"
    missing = "T.I has no changelog/T.I.md fragment at the tip -- add one"
    monkeypatch.setattr(commit_stamp, "check", lambda *a, **k: ran.append("gate") or [ungated])
    monkeypatch.setattr(fragment_check, "missing", lambda top: [missing])
    monkeypatch.setattr(landq_early, "at_enqueue", lambda *a, **k: "")
    monkeypatch.setattr(landq_early, "settle", lambda q, p, red, log, r: settled.append((red, log)))
    monkeypatch.setattr(land_check.main_red, "notice", lambda *a: [])
    monkeypatch.setattr(land_check.cov_risk, "say", lambda *a: None)
    monkeypatch.setattr(land_check, "run", lambda *a, **k: [])

    @contextlib.contextmanager
    def unguarded(*a, **k):
        yield type("Job", (), {"rc": None})()

    monkeypatch.setattr(check_jobs, "guarded", unguarded)
    req = {"repo": str(tree), "worktree": str(tree), "sha": "f" * 40, "item": "T.I"}

    def enqueue() -> str:
        return land_check.at_enqueue(tmp_path, dict(req), lambda *a: tmp_path, None, said.append)

    assert "changelog fragment of T.I" in enqueue()
    assert ran == [], "a tree that declares no gate has no commit gate"
    (tree / ".gatesrc").write_text("")
    assert "the commit gate on 0123456789 refused" in enqueue()
    red, log = settled[-1]
    assert red["guards"] == ["the commit gate on 0123456789", "the changelog fragment of T.I"]
    assert "found X" in log and missing in log, log
    monkeypatch.setattr(commit_stamp, "check", lambda *a, **k: [])
    monkeypatch.setattr(fragment_check, "missing", lambda top: [])
    assert enqueue() == ""
