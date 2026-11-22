"""goh obeys its own scanners: the registry walked over goh with an empty seed (GOH.CLASS.SELF).

The walk and its seed are described in `_self_walk.py`. Here: every direction of the ratchet on a
synthetic result (fast), then two real walks run side by side -- goh as it stands, which must match
`tests/self_walk_seed.json` exactly, and goh with one violation planted for each of six scanners of
different kinds (a native line scan, a native shell scan, a delegated AST scan run as its own
command, an opt-in the walk turns on), each of which must read red. The walks run in PARTS, one
case each, so xdist spreads them (GOH.SELFWALKFILTER): one whole walk was 56-70 s in one case.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import _self_walk as w
import pytest
from _self_walk import Result
from conftest import hermetic_env

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


# A file the planted tree EDITS (a comment appended, no scanner's business): its bytes differ
# between the trees, so its verdict is the planted tree's own and it is linted again there.
EDITED = "checks/check_no_screen_linkage.sh"

# Every shellcheck the walks spawn, logged by its last argument (the file), then run for real.
SHIM = '#!/bin/sh\nfor a; do last=$a; done\nprintf "%s\\n" "$last" >> {log}\nexec {real} "$@"\n'

# THE PARTS of the walk, one case each (GOH.SELFWALKFILTER). The dear steps are named, so no part
# holds two of them (measured 2026-11-21, debug build: kill-by-name 9.9 s, unreaped 5.1, emoji 4.7,
# home paths 4.0, claims 3.2); every other carried step is in `rest`, every other scanner (a
# `goh <cmd>`) in `commands`, so a scanner added to the registry is walked with no edit here.
SUBJECT = "every scanner read its declared subject"
SHELL = "shell"
PARTS = {
    "kill-by-name": ["no process kill by name"],
    "unreaped": ["no unreaped spawns in tests", "no committed secrets"],
    "emoji": ["no disallowed emoji", "no direct write to a Homebrew tap", "markdown links resolve"],
    "home": [
        "no hard-coded home paths",
        "prose claims are derived",
        "no busy loop or busy-wait in a tracked file",
    ],
    # The shell-source steps, and the session's ONE cold shell lint (`test_goh_obeys_...[shell]`).
    SHELL: [
        "shell lint",
        "no early-exit pipe under pipefail",
        "no code after exec",
        "no bare read of the hook's index",
        "no release step hides its output",
        "no BSD-only mktemp -t",
        "no bare open of an app",
        "no temp, lock or stage path in a work tree",
        "no git stopped at a deadline holding a lock",
        "no test endpoint on a literal ephemeral port",
        "no hedged plural in a string",
        "no hand-rolled TUI mark",
        "no bare python in a hook or gate",
    ],
}
REST, COMMANDS = "rest", "commands"


def parts(labels: list[str], steps: set[str]) -> dict[str, list[str]]:
    """Every registry label in exactly one part. SUBJECT judges what the steps before it in ITS run
    read, so it rides in every part that runs steps: a scanner's read is judged in the run that
    made it, and a red in any part is a red in the whole."""
    named = {label for part in PARTS.values() for label in part} | {SUBJECT}
    out = {name: list(part) for name, part in PARTS.items()}
    out[REST] = [lb for lb in labels if lb in steps and lb not in named]
    out = {name: [*part, SUBJECT] for name, part in out.items()}
    out[COMMANDS] = [lb for lb in labels if lb not in steps and lb not in named]
    return out


@pytest.fixture(scope="module")
def trees(native_goh: Path, tmp_path_factory) -> dict:
    """goh as it stands and goh with PLANTS, cloned once per worker; each part walks both.

    No part lints the shell sources but SHELL, and SHELL lints the clean tree ONCE before either of
    its walks (GOH.SELFWALKSC: two full walks paid two shellchecks for every file both trees hold
    byte for byte, 742 spawns, 745 CPU-s in one land, 2026-11-19), into ONE verdict store the trees
    share, the one shell-lint keys by the file's bytes, its sources, the tools and the flags."""
    seed = w.load_seed()
    reg = w.registry(native_goh)
    steps = w.carried(native_goh)
    root = tmp_path_factory.mktemp("self-walk")
    plants = dict(PLANTS.values())
    plants[EDITED] = (w.REPO_ROOT / EDITED).read_text() + "# edited by the planted walk\n"
    real = shutil.which("shellcheck")
    assert real, "the walk needs shellcheck on PATH"
    shim, log = root / "shim", root / "shellcheck.log"
    shim.mkdir()
    (shim / "shellcheck").write_text(SHIM.format(log=log, real=real))
    (shim / "shellcheck").chmod(0o755)
    env = hermetic_env(GOH_VERDICT_DIR=str(root / "verdicts"),
                       PATH=f"{shim}{os.pathsep}{os.environ.get('PATH', '')}")  # fmt: skip
    built = {}
    for name, planted in (("goh", {}), ("planted", plants)):
        tree = w.walked_tree(root / name, seed, planted)
        built[name] = (tree, w.walk_env(env, root / f"{name}-env"))
    labels = [r["scanner"] for r in reg]
    return {"labels": labels, "steps": steps, "parts": parts(labels, steps), "root": root,
            "log": log, **built}  # fmt: skip


def test_the_parts_hold_every_scanner_once_and_name_no_other(trees) -> None:
    """A scanner walked in no part is a scanner the walk stopped judging; one walked in two is
    judged twice against one seed count. A named part's label the registry dropped is stale."""
    labels, split = trees["labels"], trees["parts"]
    flat = [lb for part in split.values() for lb in part if lb != SUBJECT]
    assert sorted(flat) == sorted(set(labels) - {SUBJECT}), "a scanner in no part, or in two"
    assert all(SUBJECT in part for name, part in split.items() if name != COMMANDS)
    stale = {lb for part in PARTS.values() for lb in part} - set(labels)
    assert not stale, f"a part names no scanner in the registry: {sorted(stale)}"
    assert all(lb in trees["steps"] for name in PARTS for lb in PARTS[name]), (
        "a named step not carried"
    )


def test_every_seed_entry_names_a_scanner(trees) -> None:
    """`restrict` judges each part on its own entries; an entry for no scanner is judged here."""
    every_ran = {lb: Result(ran=True) for lb in trees["labels"]}
    reds = w.judge(trees["labels"], every_ran, w.load_seed())
    assert not [r for r in reds if "is no scanner in the registry" in r or "no why" in r], reds


@pytest.mark.parametrize("part", [*PARTS, REST, COMMANDS])
def test_goh_obeys_every_scanner_it_ships_but_for_its_seed(native_goh, trees, part) -> None:
    """THE CLOSURE, one part at a time: each of its scanners over goh, no seed, no grace; done is no
    findings. The walk covers the part (each scanner ran, or not_run says why), and THE CALIBRATION:
    each plant in the part raises its own scanner's count over the clean walk."""
    labels, steps = trees["parts"][part], trees["steps"]
    clean, planted = trees["goh"], trees["planted"]
    if part == SHELL:
        w.lint(native_goh, *clean)  # before either walk: the planted walk reads its verdicts
    with ThreadPoolExecutor(2) as pool:
        got = pool.map(lambda te: w.walk(native_goh, *te, labels, steps, part), (clean, planted))
        walked, sown = got
    seed = w.restrict(w.load_seed(), labels)
    reds = w.judge(labels, walked, seed)
    assert reds == [], "goh breaks its own rules (tests/self_walk_seed.json):\n" + "\n".join(reds)
    ran = {n for n, r in walked.items() if r.ran}
    assert set(labels) == ran | set(seed["not_run"])
    must = {"shell lint", "goh status-markers", "no hand-rolled TUI mark"} & set(labels)
    assert must <= ran
    mine = [n for n in PLANTS if n in labels]
    risen = {n for n in mine if sown[n].count > walked.get(n, Result()).count}
    assert risen == set(mine)
    sown_reds = w.judge(labels, sown, seed)
    for name in mine:
        assert any(r.startswith(f"`{name}` finds") for r in sown_reds), name
    if part == SHELL:
        _shellchecks_each_file_once(w.shellchecked(trees["log"], trees["root"]))


def _shellchecks_each_file_once(by_file: dict[str, Counter[str]]) -> None:
    """GOH.SELFWALKSC: a file both trees hold byte for byte is linted ONCE in the run; one whose
    bytes differ (EDITED) or that only the planted tree holds is linted in the planted tree."""
    assert by_file, "the walks spawned no shellcheck: the shim is not on their PATH"
    twice = {f for f, trees in by_file.items() if len(trees) > 1}
    assert twice == {EDITED}, f"linted in both trees: {sorted(twice)[:8]} ({len(twice)})"
    assert all(n == 1 for trees in by_file.values() for n in trees.values()), "a tree re-linted"
    assert by_file[PLANTS["no code after exec"][0]] == Counter(planted=1)
