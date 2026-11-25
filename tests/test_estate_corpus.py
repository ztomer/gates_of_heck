"""The R3 sweep: house checkers run against corpora taken from the REAL estate.

SUPERSOTA R3 — "a gate that cannot fail in the shape it was written for is not proven". The mechanism
that already existed for this lives in another estate
(`games/game_asset_factory/tools/check_gate_calibration.py`); what is here is the part that can be
checked from this side: every declared checker, handed a real subtree copied out of a real consumer
repo, must still go red on a violation planted inside it.

These tests pin the mechanism rather than the corpus: the corpus is the estate, which changes, and
a test that asserted "storage-server has ≥5 files" would be a test about today. What must hold is
that the sweep FAILS when it should, and that a real entry really runs.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from _gatesrc_kit import as_fixture
from conftest import REPO_ROOT as ROOT, commit_all, git, write

CHECK = ROOT / "checks" / "check_estate_corpus.py"

sys.path.insert(0, str(ROOT / "checks"))
import check_estate_corpus as sweep

sys.path.insert(0, str(ROOT / "lib"))
import not_applicable


def run_gate(*args):
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_probe_passes():
    """Where the first real corpus is absent, a hosted run's probe says NOT APPLICABLE (exit 78)
    and still proves its third case; anywhere else it must pass whole (lib/not_applicable.py)."""
    got = run_gate("--probe")
    said = got.stdout + got.stderr
    if not os.path.isdir(os.path.expanduser(sweep.ESTATE[0]["root"])) and not_applicable.hosted():
        assert got.returncode == not_applicable.EXIT, said
        assert not_applicable.MARK in said and "a corpus below the floor is refused" in said, said
        return
    assert got.returncode == 0, said


def test_every_entry_names_a_corpus_that_is_a_corpus():
    """A declared root that is not on this machine is reported and skipped, never counted. A path
    typo would otherwise turn an entry into a permanent silent skip -- the estate's own version of
    a fixture."""
    for entry in sweep.ESTATE:
        # Home-relative, so it names the same corpus on any machine: the old `here or "~" not in
        # root` inverted this, held only where every corpus was present, and failed on a hosted
        # runner whose home had none (run 42944992082).
        assert entry["root"].startswith(("~/", "store:")), (
            f"{entry['checker']}: a hard-coded absolute path"
        )
        assert entry["why"].strip(), f"{entry['checker']}: no reason recorded"
        assert entry["ext"].startswith("."), f"{entry['checker']}: no language to plant into"


def test_an_absent_estate_is_reported_not_counted(tmp_path):
    entry = {
        "checker": "check_no_conflict_markers.py",
        "root": str(tmp_path / "not-here"),
        "scope": (".",),
        "ext": ".py",
        "plant": "x\n",
        "args": (),
        "why": "probe",
    }
    findings = []
    assert sweep.judge(entry, findings) == "unavailable"
    assert findings == [], "an absent corpus must not be a finding, and must not be a pass either"


def test_a_store_root_is_read_from_the_private_store_never_the_tree(tmp_path, monkeypatch):
    """GOH.PRIVNAMES3: a corpus in a PRIVATE repository is named in this public tree by a label,
    `store:<label>`, and its path lives in the owner's store (`estate_corpus_roots.json` beside
    `private_names.txt`). No store, or no such label, is `unavailable` on a named warning line --
    the same as a corpus not on this machine -- never a pass and never a finding."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    store = tmp_path / "seeds"
    store.mkdir()
    monkeypatch.setenv("GOH_SEED_DIR", str(store))
    assert sweep.resolve_root("~/x") == os.path.expanduser("~/x")
    assert sweep.resolve_root("store:crates") is None
    (store / sweep.STORE_ROOTS).write_text(f'{{"roots": {{"crates": "{corpus}"}}}}')
    assert sweep.resolve_root("store:crates") == str(corpus)
    assert sweep.resolve_root("store:other") is None
    (store / sweep.STORE_ROOTS).write_text("not json")
    assert sweep.resolve_root("store:crates") is None
    entry = {
        "checker": "check_no_conflict_markers.py",
        "root": "store:absent",
        "scope": (".",),
        "ext": ".py",
        "plant": "x\n",
        "args": (),
        "why": "probe",
    }
    findings = []
    assert sweep.judge(entry, findings) == "unavailable"
    assert findings == []


def test_a_corpus_below_the_floor_is_refused(tmp_path):
    """A "real corpus" that lost its bulk is the failure this gate exists to catch, reproduced
    inside the gate."""
    thin = tmp_path / "thin"
    (thin / "src").mkdir(parents=True)
    (thin / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "c@e.invalid"),
        ("config", "user.name", "c"),
        ("add", "-A"),
    ):
        subprocess.run(
            ["git", "-C", str(thin), *args],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    findings = []
    state = sweep.judge(
        {
            "checker": "check_no_empty_assert.py",
            "root": str(thin),
            "scope": ("src",),
            "ext": ".rs",
            "plant": "fn probe() {}\n",
            "args": (),
            "why": "probe",
        },
        findings,
    )
    assert state == "blind"
    assert findings == ["check_no_empty_assert.py"]


def test_a_corpus_that_is_not_clean_is_refused(tmp_path):
    """The attribution rule: a red the corpus produced BEFORE the plant cannot be credited to the
    plant. Found by replaying the 2026-10-02 receiver bug -- the sweep reported 6/6 green while the
    checker was blind, because the corpus carried a finding of its own and named the file anyway.
    """
    noisy = tmp_path / "noisy"
    (noisy / "src").mkdir(parents=True)
    (noisy / "src" / "lib.rs").write_text(
        "pub fn main() {\n" + "".join(f"    let v{i} = {i};\n" for i in range(12)), encoding="utf-8"
    )
    for args in (
        ("init", "-q"),
        ("config", "user.email", "c@e.invalid"),
        ("config", "user.name", "c"),
        ("add", "-A"),
    ):
        subprocess.run(
            ["git", "-C", str(noisy), *args],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    # A file of the corpus's own making that the checker refuses whatever else happens.
    with open(noisy / "src" / "lib.rs", "a", encoding="utf-8") as handle:
        handle.write("\n// conflict marker below\n<<<<<<< HEAD\na\n=======\nb\n>>>>>>> x\n")
    findings = []
    state = sweep.judge(
        {
            "checker": "check_no_conflict_markers.py",
            "root": str(noisy),
            "scope": ("src",),
            "ext": ".rs",
            "plant": "fn probe() {}\n",
            "args": (),
            "why": "probe",
        },
        findings,
    )
    assert state == "blind"
    assert findings == ["check_no_conflict_markers.py"]


def test_a_path_the_consumer_exempts_is_not_judged_for_it(repo):
    """The corpus is judged the way its owner's gate judges it: with the owner's `GOH_EXCLUDE`.
    app_updates vendored a crate whose docs carry a dead anchor, excluded it, and was green under
    its own gate while this sweep called the corpus "NOT clean" and went red (2026-10-08). The
    excluded tree sorts FIRST here, so the plant must also skip it to land in judged code."""
    body = "".join(f"line {i}\n" for i in range(12))
    files = {
        ".gatesrc": "GOH_MAX_LINES=500  # cap\nexport GOH_EXCLUDE='^A_vendor/'  # third-party\n",
        "A_vendor/x/PROTOCOL.md": body + "See [nothing](#no-such-anchor).\n",
        "README.md": body + "See [the guide](docs/guide.md).\n",
        **{f"docs/{n}.md": body for n in ("guide", "a", "b", "c")},
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(as_fixture(rel, text), encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert sweep.consumer_exclude(str(repo)) == "^A_vendor/"
    findings = []
    state = sweep.judge(
        {
            "checker": "md-links",
            "root": str(repo),
            "scope": (".",),
            "ext": ".md",
            "plant": "\nSee [the probe](./ZZProbeDoesNotExist.md).\n",
            "args": (),
            "why": "probe",
        },
        findings,
    )
    assert (state, findings) == ("verified", [])


@pytest.mark.parametrize("index", range(len(sweep.ESTATE)))
def test_a_declared_entry_verifies_against_the_real_estate(index):
    """One entry per worker: each builds its corpus and runs its checker. Skipped, not failed, when
    the estate is not on this machine -- reported by the sweep itself, and asserted here so the skip
    is visible in the suite rather than silent."""
    entry = sweep.ESTATE[index]
    if not os.path.isdir(sweep.resolve_root(entry["root"]) or ""):
        pytest.skip(f"{entry['root']} is not on this machine")
    findings = []
    assert sweep.judge(entry, findings) == "verified", findings


def test_a_corpus_borrows_its_owners_objects_and_its_commit_is_whole(repo, tmp_path):
    """GOH.FSEVQUIET: the scratch corpus wrote every object the owner has already committed. It
    reads the owner's, and its commit holds what it copied, the uncommitted edit too."""
    write(repo, "src/a.py", "a = 1\n" * 50)
    write(repo, "src/b.py", "b = 1\n" * 50)
    git(repo, "add", "-A")
    commit_all(repo, "two files")
    write(repo, "src/b.py", "b = 2\n" * 50)  # not committed by its owner
    dest = tmp_path / "corpus"
    sweep.materialise(str(repo), ["src/a.py", "src/b.py"], str(dest))
    assert git(dest, "show", "HEAD:src/b.py") == "b = 2\n" * 50
    assert git(dest, "show", "HEAD:src/a.py") == "a = 1\n" * 50
    own = [p.parent.name + p.name for p in (dest / ".git/objects").glob("??/*")]
    blob = git(dest, "rev-parse", "HEAD:src/a.py").strip()
    assert blob not in own and git(dest, "rev-parse", "HEAD:src/b.py").strip() in own
