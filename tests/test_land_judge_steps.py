"""GOH.ONEGATE: main's version of each gate step a lane changed, over the lane's tree.

Each plant is named by the judge: a lane that weakens a step, breaks one, deletes one, drops one
from the list, or replaces the land's gate; an unchanged step is not run. One pair of small roots
serves the file; a verdict that needs a real process is in tests/test_land_driver.py.
"""

from __future__ import annotations

from pathlib import Path

import land_judge_steps as steps
import pytest
from land_judge_rows import Step

WEAK, SAME, BROKE, GONE, LESS = (
    f"./gates/{n}.sh" for n in ("weak", "same", "broke", "gone", "less")
)
MAIN, TIP = [WEAK, SAME, BROKE, GONE, LESS], [WEAK, SAME, BROKE, GONE, "./gates/less.sh --fast"]
REFUSES = "x\n✗ [weak] bad.txt is in the tree\n"


@pytest.fixture(scope="module")
def roots(tmp_path_factory) -> tuple[Path, Path]:
    """Main's export and the tip's: a step's script in each, its text the closure's content."""
    both = []
    for who, texts in (
        ("main", dict.fromkeys(("weak", "same", "broke", "gone", "less"), "as main has it")),
        ("tip", {"weak": "weakened", "same": "as main has it", "broke": "broken", "less": "x"}),
    ):
        root = tmp_path_factory.mktemp("roots") / who
        (root / "gates").mkdir(parents=True)
        for name, text in texts.items():
            (root / "gates" / f"{name}.sh").write_text(text)
        both.append(root)
    return both[0], both[1]


def _text(step: str, root: Path) -> str:
    return "".join((root / f).read_text() for f in steps.scripts(step, root))


def _lists(monkeypatch, roots, theirs=None, gates=("", "")) -> None:
    said = {roots[0]: (MAIN, gates[0]), roots[1]: (theirs or TIP, gates[1])}
    monkeypatch.setattr(steps, "declared", said.__getitem__)


class Runs:
    """The steps run: (whose code, script) and what each is told to answer, main's first."""

    def __init__(self, main: Path, **rcs) -> None:
        self.main, self.rcs, self.seen, self.env = (
            main,
            {k: list(v) for k, v in rcs.items()},
            [],
            [],
        )

    def __call__(self, cmd: str, env: dict) -> tuple[int | None, str]:
        who = "main" if str(self.main) in cmd else "tip"
        assert env["GOH_DIR"].endswith(who), (cmd, env["GOH_DIR"])
        name = cmd.split("gates/")[1].split(".sh")[0]
        self.seen.append((who, name))
        self.env.append(env)
        rc, text = self.rcs.get(f"{who}_{name}", [(0, "")]).pop(0)
        return rc, text


def _judge(roots, run, red=None, landed=True, kind=None) -> dict[str, Step]:
    choices = steps.choose(*roots, digest=_text)
    if kind:
        choices = [c for c in choices if c.kind == kind]
    got = steps.judge(choices, *roots, "b" * 40, roots[0], red or {}, run, landed=landed)
    return {s.step: s for s in got}


def test_each_plant_is_named_and_an_unchanged_step_is_not_run(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots)
    run = Runs(roots[0], main_weak=[(1, REFUSES), (1, REFUSES)], tip_weak=[(0, "")],
               main_gone=[(0, "")], main_less=[(1, "✗ less: red\n")],
               main_broke=[(0, "")])  # fmt: skip
    got = _judge(roots, run, red={BROKE: 2})
    assert {s: (v.outcome, v.main_rc, v.tip_rc) for s, v in got.items()} == {
        WEAK: ("would_red", 1, 0), SAME: ("same", None, None), BROKE: ("broken", 0, 2),
        GONE: ("deleted", 0, None), LESS: ("dropped", 1, None)}  # fmt: skip
    assert got[WEAK].reason == "✗ [weak] bad.txt is in the tree"
    assert "no gates/gone.sh" in got[GONE].reason and "green" in got[GONE].reason
    assert "`./gates/less.sh --fast` in its place" in got[LESS].reason
    assert "red: ✗ less: red" in got[LESS].reason
    assert run.seen == [("main", "weak"), ("main", "weak"), ("tip", "weak"), ("main", "broke"),
                        ("main", "gone"), ("main", "less")]  # fmt: skip


def test_a_red_is_said_by_its_finding_and_its_step() -> None:
    text = "ok\n✗ /m/lib/x.sh:1: a finding\nnoise\n✗ another\n✗ structural: a check failed\nend\n"
    assert steps.said(text, "/m") == "✗ lib/x.sh:1: a finding | ✗ structural: a check failed"
    assert steps.said("a\n✗ one\nz\n") == "✗ one" and steps.said("a\nz\n") == "z"
    assert steps.said("") == "no output"


def test_a_changed_step_main_passes_is_green_after_one_run(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots)
    run = Runs(roots[0], main_weak=[(0, "")], main_broke=[(0, "")])
    got = _judge(roots, run, kind="changed")
    assert [(v.outcome, v.main_rc) for v in got.values()] == [("green", 0)] * 2
    assert run.seen == [("main", "weak"), ("main", "broke")]


@pytest.mark.parametrize(
    ("main", "tip", "outcome"),
    [
        ([(1, "✗ a"), (0, "")], [], "flaky"),
        ([(1, "✗ a"), (1, "✗ a")], [(1, "✗ a")], "both_red"),
        ([(1, "✗ a"), (1, "✗ a")], [(None, "it passed its deadline")], "unavailable"),
        ([(None, "it passed its deadline of 1800 s")], [], "unavailable"),
        ([(127, "no such file")], [], "unavailable"),
        ([(78, "not here")], [], "unavailable"),
        ([(-9, "")], [], "unavailable"),
    ],
)
def test_a_pair_is_read_as_what_it_shows(monkeypatch, roots, main, tip, outcome) -> None:
    _lists(monkeypatch, roots)
    run = Runs(roots[0], main_weak=main, tip_weak=tip)
    got = _judge(roots, run, landed=False, red={WEAK: 0})[WEAK]
    assert got.outcome == outcome and (outcome != "unavailable" or got.reason)


def test_a_red_land_is_judged_on_what_its_own_gate_had_red(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots)
    run = Runs(roots[0], main_broke=[(0, "")], main_gone=[(0, "")], main_less=[(0, "")])
    got = _judge(roots, run, red={BROKE: 1}, landed=False)
    assert WEAK not in got and got[BROKE].outcome == "broken"


def test_a_dropped_step_stands_where_mains_version_gave_no_verdict(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots)
    got = _judge(
        roots, Runs(roots[0], main_less=[(None, "it passed its deadline")]), kind="dropped"
    )
    assert got[LESS].outcome == "dropped" and "deadline" in got[LESS].reason


def test_a_lane_that_replaces_the_lands_gate_dropped_the_list(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots, gates=("", "true"))
    got = _judge(roots, Runs(roots[0]), kind="dropped")
    assert got[steps.GATE].outcome == "dropped" and "`true`" in got[steps.GATE].reason


def test_a_step_with_no_closure_is_unavailable_not_unchanged(monkeypatch, roots) -> None:
    _lists(monkeypatch, roots)

    def unread(_step: str, _root: Path) -> str:
        raise OSError("unreadable")

    got = steps.judge(
        steps.choose(*roots, digest=unread), *roots, "b", roots[0], {}, Runs(roots[0])
    )
    assert {s.step: s.outcome for s in got}[SAME] == "unavailable"


def test_a_step_as_mains_code_runs_it() -> None:
    root = Path(steps.__file__).parent.parent
    run = lambda s: steps.command(s, root, "c0ffee", Path("/s"))  # noqa: E731
    assert run("./gates/goh.sh shape-diff . --estate --since main") == (
        f"{root}/gates/goh.sh shape-diff . --estate --since c0ffee")  # fmt: skip
    two = '"$GOH_PY" ./gates/path_invariance.py -- ./gates/structural.sh --full'
    gates = f"{root}/gates"
    assert run(f"[db] {two}") == two.replace("./gates", gates)
    assert run("cargo test --workspace --doc --locked") == "cargo test --workspace --doc --locked"
    assert run("$GOH_DIR/gates/structural.sh") == "$GOH_DIR/gates/structural.sh"
    assert run("./gates/no_such.sh") == "./gates/no_such.sh"
    assert run("./scripts/release_canary.py").endswith('--goh "$PWD" --state /s/estate_audit.json')


def test_a_steps_environment_is_mains_and_nothing_a_land_handed_down(monkeypatch, tmp_path) -> None:
    for k in (
        "GOH_BIN",
        "GOH_RESOLVED_BIN",
        "GOH_COV_GOH",
        "GOH_LAND_VERDICTS_REUSE",
        "GIT_DIR",
        "GOH_TIMINGS",
        "GOH_LIVE",
        "GOH_HELD_LOCKS",
    ):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("PYTHONPATH", "/lane:/m")
    env = steps.environ(Path("/m"), tmp_path)
    assert env["PYTHONPATH"] == "/m:/lane" and env["GOH_DIR"] == "/m"
    assert env["GOH_PROVEN"] == "0" and env["TMPDIR"] == str(tmp_path)
    assert not [k for k in env if steps.DROP.match(k)]


def _load(monkeypatch, rc: int, started: bool, out: str = "", said: str = "") -> list:
    seen = []

    def run(argv, **kw) -> object:
        seen.append((argv, kw))
        mark, cmd, log = argv[-3:]
        assert cmd == "./s"
        if started:
            Path(mark).touch()
            Path(log).write_text(out)
        return type("Done", (), {"returncode": rc, "stdout": said, "stderr": ""})

    monkeypatch.setattr(steps.subprocess, "run", run)
    return seen


def test_a_step_runs_in_a_slot_of_its_own_as_class_work(monkeypatch, tmp_path) -> None:
    seen = _load(monkeypatch, 3, True, "✗ red\n", "load: gate slot taken")
    run = steps.InSlot(tmp_path, tmp_path, lambda: None)
    assert run("./s", {"A": "1"}) == (3, "✗ red\n")  # its own exit 3, started: no slot's
    ((argv, kw),) = seen
    assert argv[1].endswith("tools/load.py") and argv[argv.index("--") - 1] == "0"
    assert kw["env"] == {"A": "1", "GOH_GATE_CLASS": "work"} and kw["cwd"] == tmp_path
    assert run.ran + run.waited >= 0


@pytest.mark.parametrize(
    ("rc", "started", "why"),
    [(1, False, "did not start: refused"),
     (124, True, "passed its deadline")],
)  # fmt: skip
def test_what_never_ran_or_never_ended_gave_no_verdict(
    monkeypatch, tmp_path, rc, started, why
) -> None:
    _load(monkeypatch, rc, started, "", "refused")
    (tmp_path / "started").touch()  # the step before it: its mark is not this one's
    got, said = steps.InSlot(tmp_path, tmp_path, lambda: None)("./s", {})
    assert got is None and why in said


def test_room_is_asked_before_every_step_and_again_when_no_slot_came(monkeypatch, tmp_path) -> None:
    order, left = [], [3, 3, 0]
    run = steps.InSlot(tmp_path, tmp_path, lambda: order.append("room"))

    def load(argv, **_kw) -> object:
        order.append("slot")
        rc = left.pop(0)
        if rc == 0:
            Path(argv[-3]).touch()
            Path(argv[-1]).write_text("green")
        return type("Done", (), {"returncode": rc, "stdout": "", "stderr": ""})

    monkeypatch.setattr(steps.subprocess, "run", load)
    assert run("./s", {}) == (0, "green") and order == ["room", "slot"] * 3
    left.append(0)
    run("./s", {})
    assert order == ["room", "slot"] * 4


def test_no_room_past_its_bound_ends_the_judging_before_the_step(monkeypatch, tmp_path) -> None:
    def never() -> None:
        raise TimeoutError

    monkeypatch.setattr(steps.subprocess, "run", lambda *_a, **_k: pytest.fail("a step ran"))
    with pytest.raises(TimeoutError):
        steps.InSlot(tmp_path, tmp_path, never)("./s", {})


def test_the_lists_are_read_from_the_file_and_none_is_an_error(tmp_path) -> None:
    (tmp_path / ".gatesrc").write_text("GOH_CI_STEPS='a: ./b'\nGOH_LAND_GATE=g\n")
    assert steps.declared(tmp_path) == (["a", "./b"], "g")
    (tmp_path / ".gatesrc").write_text("GOH_OTHER=1\n")
    with pytest.raises(LookupError, match="no GOH_CI_STEPS"):
        steps.declared(tmp_path)


def test_the_digest_moves_with_the_steps_closure(monkeypatch) -> None:
    files = {"/main": "1", "/tip": "1"}
    closure = [(["gates/a.sh"], "")]
    monkeypatch.setattr(steps.step_closure, "step_closure", lambda *_: closure[0])
    monkeypatch.setattr(steps.step_closure, "_part_hash", lambda r, _f: files[str(r)])
    monkeypatch.setattr(steps.gate_identity, "identity", lambda r: f"whole {r}")
    two = lambda: [steps.digest("s", Path(r)) for r in files]  # noqa: E731
    assert len(set(two())) == 1
    files["/tip"] = "2"
    assert len(set(two())) == 2
    closure[0] = ([], "it runs the goh binary")
    assert two() == ["full whole /main", "full whole /tip"]
