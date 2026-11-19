"""The fault item a reuse path turned off files (lib/reuse_fault.py, GOH.REUSEAUDITFILE,
GOH.REUSEOFFLANDED2, GOH.REUSEOFFLANDED3): named by class, under its phase's contract, into the
queue's current phase, and a new id for each off. Split from tests/test_reuse_audit.py, whose
fake loopctl and store it uses."""

from __future__ import annotations

import json
import subprocess

import reuse_audit
from _fast_git import fast_init
from test_reuse_audit import FIELDS, fake_loopctl, filed, store  # noqa: F401  (fixtures)


def _off_by_a_repeated_red(fresh_from: str = "") -> None:
    """`landed` off the way a release push's sample turns it off: a red full gate, red again."""
    reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "red"),
                        fresh_from=fresh_from)  # fmt: skip


def test_a_path_turned_off_files_its_fault_under_class_first(
    fake_loopctl,  # noqa: F811
    tmp_path,
) -> None:
    """GOH.REUSEAUDITFILE: the fault went to loopctl with no class, rules.class_first refused it,
    and the record only said "fault not filed". It is filed as `class_of: none`, naming the path."""
    _off_by_a_repeated_red()
    rec = reuse_audit.state("landed")
    assert rec["state"] == "off" and rec["fault"] == "filed GOH.REUSEOFFLANDED", rec
    [fault] = filed(fake_loopctl)
    assert fault["class_of"] == "none" and "'landed'" in fault["class_why"], fault


def test_a_fault_files_under_a_phase_that_requires_metric_and_predicted(
    fake_loopctl,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    """GOH.REUSEOFFLANDED3: `landed` turned off and loopctl refused its fault, "phase SPD requires
    `predicted` on its open items": the record carried neither field SPD's contract names. It
    carries both, from what the audit knows: the path's skip, and that it is off until enabled."""
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"phases": [{"id": "SPD", "requires": ["metric", "predicted"]}]}))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(queue))
    _off_by_a_repeated_red()
    assert reuse_audit.state("landed")["fault"] == "filed GOH.REUSEOFFLANDED"
    [fault] = filed(fake_loopctl)
    assert "lib/release_gate.py" in fault["metric"] and "enable landed" in fault["predicted"], fault


def test_a_fault_files_into_the_current_open_phase_once_spd_is_closed(
    fake_loopctl,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    """GOH.REUSEOFFLANDED3: the item named phase SPD whatever the queue said, so it worked only
    while SPD was open. The phase is the queue's current one: the first open phase whose contract
    the item meets (DF wants the owner's words, which no audit has), past a closed SPD."""
    queue = tmp_path / "queue.json"
    phases = [{"id": "SPD", "closed": True}, {"id": "DF", "requires": ["owner_words"]},
              {"id": "HR"}, {"id": "OW"}]  # fmt: skip
    queue.write_text(json.dumps({"phases": phases}))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(queue))
    _off_by_a_repeated_red()
    assert reuse_audit.state("landed")["fault"] == "filed GOH.REUSEOFFLANDED"
    assert [f["phase"] for f in filed(fake_loopctl)] == ["HR"]


def test_a_second_off_after_its_item_is_done_files_a_new_item_naming_its_run(
    fake_loopctl,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    """GOH.REUSEOFFLANDED2: `landed` turned off again on v0.136.0's CI red while GOH.REUSEOFFLANDED,
    the first off's item, was done. loopctl refused the id ("already exists") and the audit read
    that refusal as filed: the record said "filed GOH.REUSEOFFLANDED" and nothing tracked the second
    disagreement. Each off follows an enable, so it is a new instance: it files the next free id,
    names the item before it, and the run its fresh side was (the pushed sha, not the key's
    parent)."""
    items = tmp_path / "queue_ids"
    items.write_text("GOH.REUSEOFFLANDED\n")
    monkeypatch.setenv("FAKE_ITEMS", str(items))
    fix = tmp_path / "fix"
    (fix / "tests").mkdir(parents=True)
    (fix / "tests" / "test_k.py").write_text("def test_k():\n    pass\n")
    fast_init(fix)
    for args in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
    ):
        subprocess.run(["git", "-C", str(fix), *args], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    for n, pushed in ((2, "f" * 40), (3, "e" * 40)):
        _off_by_a_repeated_red(f"the local full gate of {pushed}")
        rec = reuse_audit.state("landed")
        assert rec["state"] == "off" and rec["fault"] == f"filed GOH.REUSEOFFLANDED{n}", rec
        assert rec["evidence"]["fresh_from"] == f"the local full gate of {pushed}", rec
        fault = filed(fake_loopctl)[-1]
        assert fault["id"] == f"GOH.REUSEOFFLANDED{n}" and pushed in fault["item"], fault
        before = "GOH.REUSEOFFLANDED" + (str(n - 1) if n > 2 else "")
        assert before in fault["item"], fault
        reuse_audit.enable("landed", root=fix, fix="HEAD", test="tests/test_k.py::test_k")
    assert items.read_text().split() == [
        "GOH.REUSEOFFLANDED",
        "GOH.REUSEOFFLANDED2",
        "GOH.REUSEOFFLANDED3",
    ]
