"""Every scanner declares its subject (BACKLOG 4.3): `GOH_<SCANNER>_MIN_<UNIT>` in `.gatesrc`.

End to end through `goh structural --full`, the only place the floors are judged: an exclusion
that removes the whole subject reads NONE and is red under a floor, a floor one above what the
tree holds is red naming key, floor and count, a keyless `.gatesrc` passes and SAYS which floors
are missing with what this run read, a floor that does not parse is exit 2 naming its key, and a
commit (`--staged`) is never judged against a floor."""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT, commit_all, run_goh, stage, write

TEST_SRC = "def test_one():\n    assert 1 + 1 == 2\n"
KEY = "GOH_UNREAPED_SPAWN_MIN_FILES"


def _fixture(repo: Path, gatesrc: str) -> None:
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n" + gatesrc)
    write(repo, "tests/test_a.py", TEST_SRC)
    write(repo, "tests/test_b.py", TEST_SRC)
    write(repo, "README.md", "# fixture\n")
    commit_all(repo)


def _full(repo: Path):
    r = run_goh(repo, "structural", "--full")
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
    assert KEY in out and "floor of 3" in out and "read 2 test file(s)" in out, out


def test_a_keyless_gatesrc_passes_and_says_which_floors_it_lacks(repo: Path) -> None:
    _fixture(repo, "")
    code, out = _full(repo)
    assert code == 0, out
    advisory = [line for line in out.splitlines() if "declare no floor" in line]
    assert len(advisory) == 1, out
    assert f"{KEY}=2" in advisory[0], advisory
    assert "GOH_EMOJI_MIN_FILES=" in advisory[0], advisory
    # Declared floors are not omissions; `0` declares "no floor here" and silences the scanner.
    write(repo, ".gatesrc", f"GOH_MAX_LINES=500\n{KEY}=2\nGOH_EMOJI_MIN_FILES=0\n")
    commit_all(repo)
    code, out = _full(repo)
    assert code == 0, out
    assert KEY not in out and "GOH_EMOJI_MIN_FILES" not in out, out


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
