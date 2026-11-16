"""goh obeys its own scanners: the registry walked over goh with an empty seed (GOH.CLASS.SELF).

The walk and its seed are described in `_self_walk.py`. Here: every direction of the ratchet on a
synthetic result (fast), then two real walks run side by side -- goh as it stands, which must match
`tests/self_walk_seed.json` exactly, and goh with one violation planted for each of six scanners of
different kinds (a native line scan, a native shell scan, a delegated AST scan run as its own
command, an opt-in the walk turns on), each of which must read red.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _self_walk as w
import pytest
from _self_walk import Result
from conftest import hermetic_env

# The two walks are one module fixture: its tests share one worker, or each worker walks twice.
pytestmark = pytest.mark.xdist_group("self-walk")

LABELS = ["a", "b", "c", "goh d"]


def _seed(**over: dict) -> dict:
    base = {"enable": {}, "not_run": {}, "unjudged": {}, "findings": {}}
    return {**base, **over}


def _ran(*sites: str, failed: bool = True, unjudged: bool = False) -> Result:
    return Result(ran=True, failed=failed and bool(sites), unjudged=unjudged, sites=set(sites))


CLEAN = {name: _ran() for name in LABELS}


def test_a_clean_walk_against_an_empty_seed_is_green() -> None:
    assert w.judge(LABELS, CLEAN, _seed()) == []


def test_an_unseeded_finding_is_red_and_names_its_sites() -> None:
    reds = w.judge(LABELS, {**CLEAN, "a": _ran("x.sh:3")}, _seed())
    assert len(reds) == 1 and "`a` finds 1 over goh, seeded 0" in reds[0] and "x.sh:3" in reds[0]


def test_a_finding_over_its_count_is_red_and_one_under_it_says_lower_it() -> None:
    seed = _seed(findings={"a": {"count": 1, "why": "w"}, "b": {"count": 3, "why": "w"}})
    reds = w.judge(LABELS, {**CLEAN, "a": _ran("x:1", "y.py:2"), "b": _ran("z.rs:9")}, seed)
    assert any("`a` finds 2 over goh, seeded 1" in r for r in reds)
    assert any("`b` finds 1 over goh, seeded 3: lower the seed" in r for r in reds)


def test_a_seeded_scanner_gone_clean_is_a_stale_entry() -> None:
    reds = w.judge(LABELS, CLEAN, _seed(findings={"c": {"count": 2, "why": "w"}}))
    assert reds == ["`c` finds 0 over goh, seeded 2: lower the seed (delete the entry)"]


def test_a_red_that_names_no_site_still_counts_one() -> None:
    red = Result(ran=True, failed=True)
    assert red.count == 1
    assert w.judge(LABELS, {**CLEAN, "b": red}, _seed()) != []


def test_a_scanner_that_did_not_run_must_say_why_and_a_stale_why_is_red() -> None:
    missing = {k: v for k, v in CLEAN.items() if k != "c"}
    assert w.judge(LABELS, missing, _seed()) == [
        "`c` did not run over goh: enable it, or say why in not_run"
    ]
    assert w.judge(LABELS, missing, _seed(not_run={"c": "no subject"})) == []
    assert w.judge(LABELS, CLEAN, _seed(not_run={"c": "no subject"})) == [
        "`c` ran over goh: its not_run entry is stale, delete it"
    ]


def test_an_unjudged_scanner_is_named_both_ways() -> None:
    unjudged = {**CLEAN, "a": _ran(unjudged=True)}
    assert len(w.judge(LABELS, unjudged, _seed())) == 1
    assert w.judge(LABELS, unjudged, _seed(unjudged={"a": "no list here"})) == []
    assert len(w.judge(LABELS, CLEAN, _seed(unjudged={"a": "no list here"}))) == 1


def test_an_entry_for_no_scanner_or_with_no_why_is_red() -> None:
    reds = w.judge(LABELS, CLEAN, _seed(enable={"gone": "GOH_X=1"}))
    assert reds == ["enable: `gone` is no scanner in the registry: delete it"]
    reds = w.judge(LABELS, {**CLEAN, "a": _ran("q.py:1")}, _seed(findings={"a": {"count": 1}}))
    assert reds == ["findings: `a` carries no why"]


def test_a_log_is_read_by_step_and_a_suffixed_label_is_its_step() -> None:
    log = (
        "· file length <= 500 (≤900s)\n"
        "✓ file length <= 500\n"
        "· shell lint (≤900s)\n"
        "✗ [shell_lint] gates/a.sh#SC2034:12:3: warning\n"
        "    REV:0 is no site; lib/b.sh:4 is\n"
        "✗ structural: shell lint failed (native)\n"
        "· no private repository named in a public tree (≤900s)\n"
        "⚠ [private_names] not judged: no list\n"
    )
    names = ["file length", "shell lint", "no private repository named in a public tree", "x"]
    got = w.parse_structural(log, names)
    assert set(got) == set(names[:3]), "x never announced, so it did not run"
    assert got["file length"].count == 0 and got["file length"].ran
    assert got["shell lint"].failed and got["shell lint"].sites == {
        "gates/a.sh#SC2034:12",
        "lib/b.sh:4",
    }
    assert got["no private repository named in a public tree"].unjudged


def test_the_walk_reads_nothing_of_the_machine_beside_the_tree(tmp_path) -> None:
    """The verdict is the TREE's, the same on this Mac and a hosted runner (GOH.SELFWALKCI): the
    suite's home links the owner's ~/Projects and ~/.claude/skills (tests/suite_home.py), which a
    runner has no copy of, so `skills corpus` ran here and not there and `one roadmap` judged its
    queue rule here and not there. The walk runs as the runner does: a home with none of them."""
    env = w.walk_env(hermetic_env(), tmp_path / "goh")
    home = Path(env["HOME"])
    assert home.is_dir() and home != Path.home()
    held = [rel for rel in ("Projects", ".claude", ".claude/skills") if (home / rel).exists()]
    assert held == [], f"the walk's HOME reads the machine's {held}"
    # The estate sweep's NOT APPLICABLE is a hosted run's alone: the walk is one everywhere.
    assert env["GITHUB_ACTIONS"] == "true"


# One violation per scanner, each of a different kind. Built from pieces, so this file is none of
# them: goh's own run reads it.
PLANTS = {
    "no disallowed emoji": ("plant/emoji.txt", "a " + chr(0x1F600) + "\n"),
    "no conflict markers": (
        "plant/merge.txt",
        "<" * 7 + " HEAD\nx\n" + "=" * 7 + "\n" + ">" * 7 + " b\n",
    ),
    "no hedged plural in a string": ("plant/hedge.py", 'print("3 file' + '(s)")\n'),
    "no code after exec": ("plant/exec.sh", "#!/usr/bin/env bash\nexec true\necho never\n"),
    "goh fixed-sleep": (
        "tests/test_plant_sleep.py",
        "import time\n\n\ndef test_x(p):\n    time." + "sleep(1)\n    assert p.poll() is None\n",
    ),
    "no hard-coded home paths": ("plant/home.md", "see /Us" + "ers/someone/Projects/x\n"),
}


@pytest.fixture(scope="module")
def walks(native_goh: Path, tmp_path_factory) -> dict[str, dict[str, Result]]:
    """goh as it stands, and goh with PLANTS, walked side by side."""
    seed = w.load_seed()
    reg = w.registry(native_goh)
    root = tmp_path_factory.mktemp("self-walk")
    plants = dict(PLANTS.values())

    def one(name: str, planted: dict[str, str]) -> dict[str, Result]:
        tree = w.walked_tree(root / name, seed, planted)
        return w.walk(native_goh, tree, w.walk_env(hermetic_env(), root / f"{name}-env"), reg)

    with ThreadPoolExecutor(2) as pool:
        clean, planted = pool.submit(one, "goh", {}), pool.submit(one, "planted", plants)
        return {
            "labels": [r["scanner"] for r in reg],
            "goh": clean.result(),
            "planted": planted.result(),
        }


def test_goh_obeys_every_scanner_it_ships_but_for_its_seed(walks) -> None:
    """THE CLOSURE: every registry scanner over goh, no seed, no grace; done is no findings."""
    reds = w.judge(walks["labels"], walks["goh"], w.load_seed())
    assert reds == [], "goh breaks its own rules (tests/self_walk_seed.json):\n" + "\n".join(reds)


def test_the_walk_covers_the_whole_registry(walks) -> None:
    """No scanner falls out of the walk: each ran, or not_run says why."""
    seed = w.load_seed()
    ran = {n for n, r in walks["goh"].items() if r.ran}
    assert set(walks["labels"]) == ran | set(seed["not_run"])
    assert {"shell lint", "goh status-markers", "no hand-rolled TUI mark"} <= ran


def test_a_planted_violation_turns_its_scanner_red(walks) -> None:
    """The calibration: each plant raises its own scanner's count over the clean walk."""
    clean, planted = walks["goh"], walks["planted"]
    risen = {n for n in PLANTS if planted[n].count > clean.get(n, Result()).count}
    assert risen == set(PLANTS)
    reds = w.judge(walks["labels"], planted, w.load_seed())
    for name in PLANTS:
        assert any(r.startswith(f"`{name}` finds") for r in reds), name
