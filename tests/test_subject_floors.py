"""Every scanner declares its subject (BACKLOG 4.3): `GOH_<SCANNER>_MIN_<UNIT>` in `.gatesrc`.

End to end through `goh structural --full`, the only place the floors are judged: an exclusion
that removes the whole subject reads NONE and is red under a floor, a floor one above what the
tree holds is red naming key, floor and count, a keyless `.gatesrc` passes and SAYS which floors
are missing with what this run read, a floor that does not parse is exit 2 naming its key, and a
commit (`--staged`) is never judged against a floor."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from conftest import (
    REPO_ROOT,
    commit_all,
    git,
    hermetic_env,
    native_goh_path,
    run_goh,
    stage,
    write,
)

TEST_SRC = "def test_one():\n    assert 1 + 1 == 2\n"
KEY = "GOH_UNREAPED_SPAWN_MIN_FILES"


def _fixture(repo: Path, gatesrc: str) -> None:
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n" + gatesrc)
    write(repo, "tests/test_a.py", TEST_SRC)
    write(repo, "tests/test_b.py", TEST_SRC)
    write(repo, "README.md", "# fixture\n")
    commit_all(repo)


def _full(repo: Path, required: bool = False, **env: str):
    """`structural --full`. The seam says the day does not matter: `within` (the default here, as
    every fixture is keyless) is the advisory, `over` is the refusal the grace ends in."""
    child = hermetic_env(GOH_GRACE="over" if required else "within", **env)
    r = run_goh(repo, "structural", "--full", env=child)
    return r.returncode, r.stdout + r.stderr


def test_an_exclusion_that_removes_every_python_file_reads_none_under_a_floor(repo: Path) -> None:
    """Red-first, as the item states it: exclude every `.py` in a repo that has two."""
    _fixture(repo, f"{KEY}=2\n")
    code, out = _full(repo)
    assert code == 0, out
    # The stdin step reads the same Python and is on by default: it would go red on this blindness
    # first and stop the pipeline before the floors are judged. Its own verdict is pinned in
    # test_subprocess_stdin_scope.py; this test is the floor's.
    write(
        repo,
        ".gatesrc",
        f"GOH_MAX_LINES=500\n{KEY}=2\nGOH_EXCLUDE='\\.py$'\nGOH_SUBPROCESS_STDIN=off\n",
    )
    commit_all(repo)
    code, out = _full(repo)
    assert code == 1, out
    assert "read NONE" in out and KEY in out, out


def test_a_floor_above_what_the_tree_holds_is_the_stopped_reading_verdict(repo: Path) -> None:
    """At the count it passes; one above, it is red, naming the key, the floor and the count."""
    _fixture(repo, f"{KEY}=2\n")
    assert _full(repo)[0] == 0
    write(repo, ".gatesrc", f"GOH_MAX_LINES=500\n{KEY}=3\n")
    commit_all(repo)
    code, out = _full(repo)
    assert code == 1, out
    assert KEY in out and "floor of 3" in out and "read 2 test files" in out, out


def test_an_undeclared_floor_is_red_and_the_seam_keeps_the_old_advisory(repo: Path) -> None:
    """GOH.FLOORREQ / GOH.SCANNERFLOORS: a scanner that read something with no floor fails, naming
    the key and what it read; `GOH_GRACE=within` (the suite's seam) is the advisory."""
    _fixture(repo, "")
    code, out = _full(repo, required=True)
    assert code == 1, out
    assert f"{KEY}=2" in out and "no floor declared" in out and "GOH_EMOJI_MIN_FILES=" in out, out
    code, out = _full(repo)
    assert code == 0, out
    advisory = [line for line in out.splitlines() if "declare no floor" in line]
    assert len(advisory) == 1 and f"{KEY}=2" in advisory[0], out


def _declare_all_but(repo: Path, seeded: set[str]) -> None:
    """A `.gatesrc` declaring every undeclared key `_full` names except `seeded`, as `KEY=0`."""
    _, out = _full(repo)  # the advisory names each key it read
    keys = sorted(set(re.findall(r"(GOH_\w+_MIN_\w+)=\d+", out)))
    lines = "".join(f"{k}=0\n" for k in keys if k not in seeded)
    write(repo, ".gatesrc", f"GOH_MAX_LINES=500\n{lines}")


def test_a_seeded_undeclared_floor_warns_and_a_declared_one_is_a_stale_entry(repo: Path) -> None:
    """The ratchet, both ways (close-the-class): a key the repo's own floor seed names WARNS, an
    entry whose key is declared is stale and red, and a declared `0` is a declaration."""
    _fixture(repo, "")
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").split()[0]
    _declare_all_but(repo, {KEY})
    commit_all(repo)
    code, out = _full(repo, required=True)
    assert code == 1 and f"{KEY}=2" in out, out  # unseeded: red
    seed = {"entries": [{"root": root, "repo": "fixture", "path": KEY, "count": 1,
                         "reason": "unreviewed: declare it"}]}  # fmt: skip
    write(repo, "floor_seed.json", json.dumps(seed))
    commit_all(repo)
    code, out = _full(repo, required=True)
    assert code == 0, out  # seeded: a warning
    assert "seeded" in out and KEY in out, out
    _declare_all_but(repo, set())  # declares KEY too
    commit_all(repo)
    code, out = _full(repo, required=True)
    assert code == 1 and "stale" in out and KEY in out, out


MODES = [(), ("--full",), ("--full", "--keep-going")]


def test_a_planted_over_floor_is_red_in_every_mode_that_runs_the_scanner(repo: Path) -> None:
    """GOH.FULLFLOORS: a floor one above what the scanner read is red under the default run, under
    `--full` and under `--full --keep-going`, through the binary AND through `gates/structural.sh`
    (the hook's entry, which `tools/gate.sh --full` execs). FAULTS.SKILLS saw a planted
    GOH_EMOJI_MIN_FILES=9999 stay green under `--full`: every mode judges its floor."""
    _fixture(repo, f"{KEY}=3\n")  # the fixture holds 2 test files
    for mode in MODES:
        r = run_goh(repo, "structural", *mode, env=hermetic_env(GOH_GRACE="within"))
        out = r.stdout + r.stderr
        assert r.returncode == 1 and KEY in out and "floor of 3" in out, (mode, out)
        bash = subprocess.run(
            ["bash", str(REPO_ROOT / "gates" / "structural.sh"), *mode],
            cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
            env=hermetic_env(GOH_BIN=str(native_goh_path()), GOH_GRACE="within"),
        )  # fmt: skip
        out = bash.stdout + bash.stderr
        assert bash.returncode == 1 and KEY in out and "floor of 3" in out, (mode, out)
    write(repo, ".gatesrc", f"GOH_MAX_LINES=500\n{KEY}=2\n")
    commit_all(repo)
    for mode in MODES:  # the control: at the count, every mode is green
        r = run_goh(repo, "structural", *mode, env=hermetic_env(GOH_GRACE="within"))
        assert r.returncode == 0, (mode, r.stdout + r.stderr)


def test_a_floor_that_does_not_parse_is_exit_2_naming_its_key(repo: Path) -> None:
    _fixture(repo, f"{KEY}=lots\n")
    code, out = _full(repo)
    assert code == 2, out
    assert KEY in out, out


def test_a_commit_is_never_judged_against_a_floor(repo: Path) -> None:
    """At `--staged` a scanner reads only what the commit brings."""
    _fixture(repo, f"{KEY}=2\n")
    write(repo, "tests/test_c.py", TEST_SRC)
    stage(repo, "tests/test_c.py")
    r = run_goh(repo, "structural", "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "declared subject" not in r.stdout, r.stdout


# Scanners that do not RUN against this tree, so there is no read count to floor: the pre-push
# advisory names only scanners that ran. Each reason is a fact about `.gatesrc`, and an entry whose
# scanner starts running here (its floor then belongs in `.gatesrc`) is itself a failure.
NO_SUBJECT_HERE = {
    "GOH_JS_SYNTAX_MIN_FILES": "GOH_JS_SYNTAX=on, but the tree tracks no JavaScript: 0 files to floor",
    "GOH_FIXED_SLEEP_MIN_FILES": "goh fixed-sleep is opt-in and not switched on here",
}


def test_this_repo_declares_a_floor_for_every_scanner_that_runs_here() -> None:
    """The pre-push advisory "N scanner(s) declare no floor" was live in goh itself for
    `GOH_CHECKOUT_CREDENTIALS_MIN_FILES` (G17): a repo that owns the floors declares every one it
    runs. Every key the binary can judge (subject.rs) is a key `.gatesrc` sets, bar the scanners
    that read nothing here."""
    import re

    source = (REPO_ROOT / "crates" / "goh" / "src" / "subject.rs").read_text()
    keys = set(re.findall(r'key: "(GOH_\w+_MIN_\w+)"', source))
    declared = set(
        re.findall(r"^(GOH_\w+_MIN_\w+)=\d+", (REPO_ROOT / ".gatesrc").read_text(), re.MULTILINE)
    )
    assert keys, "no scanner subject found in subject.rs: the parse stopped matching"
    missing = keys - declared - set(NO_SUBJECT_HERE)
    assert not missing, f".gatesrc declares no floor for {sorted(missing)}"
    stale = set(NO_SUBJECT_HERE) & declared
    assert not stale, f"{sorted(stale)} now declare a floor: drop them from NO_SUBJECT_HERE"
    assert set(NO_SUBJECT_HERE) <= keys, "NO_SUBJECT_HERE names a key no scanner has"
