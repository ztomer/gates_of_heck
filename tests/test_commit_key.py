"""The key a commit gate's stamp is made of: what the gate read, and nothing it did not.

GOH.STAMPREBASE. A stamp was the commit's whole tree, so a rebase voided every stamp of a lane
(GOH.PYSLOW4: 18 commits gated again, about 400 of its 483 s enqueue check), and a tree alone
stamped a change no gate saw (a squash, an amendment). Each plant below is one commit moved by one
thing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

from lib import commit_key, commit_stamp

# Its whole read of the real tree (lib/declared_reads.py JUDGE_READS): the decisions and what their
# symbols are looked for in, and what the binary's staged run reads of HEAD's code and data. Never
# CHANGELOG.md, VERSION or a fragment: the binary's run is handed a CHANGELOG of its own.
SOURCES = ("*.py", "*.sh", "*.rs", "*.toml", "*.json", "*.tsv", ".gatesrc")
JUDGE_READS = (
    "*.py",
    "*.sh",
    "*.rs",
    "*.toml",
    "*.json",
    "*.tsv",
    ".gatesrc",
    "docs/decisions/",
    "docs/map.md",
    "docs/config/",
    "crates/",
)
TREE_READS = (
    "*.py",
    "*.sh",
    "*.rs",
    "*.toml",
    "*.json",
    "*.tsv",
    ".gatesrc",
    "docs/decisions/",
    "docs/map.md",
    "docs/config/",
    "crates/",
)

BASE = {
    "gates/commit_gate.sh": "gate\n",
    "gates/_from_head.sh": "head\n",
    "crates/goh/Cargo.toml": "[package]\n",
    ".gatesrc": "GOH_MAX_LINES=500\n",
    "run_paths_seed.json": "{}\n",
    "tests/wall_clock_seed.json": "{}\n",
    "tests/test_other.py": "def test_a(): pass\n",
    "lib/other.py": "A = 1\n",
    "lib/mine.py": "one\ntwo\nthree\n",
    "docs/contracts.md": "prose\n",
    "docs/decisions/D-0001.md": "ruled\n",
    "docs/config/GOH_X.md": "a key\n",
    "changelog/GOH.SHARED.md": "* a\n",
}
PROSE = {"docs/contracts.md": "more prose\n", "changelog/GOH.OTHER.md": "* b\n"}
LANE = [{f"lib/new{i}.py": f"N = {i}\n"} for i in range(18)]
LANE[5] = {"lib/new5.py": "N = 5\n", "changelog/GOH.SHARED.md": "* a\n* mine\n"}

Files = dict[str, str]


class Repo:
    """Commits written by `git fast-import`: no checkout, no index, one spawn a history."""

    def __init__(self, path: Path) -> None:
        self.path, self.n = path, 0

    def git(self, *args: str, stdin: str = "") -> str:
        done = subprocess.run(["git", "-C", str(self.path), *args], input=stdin, text=True,
                              capture_output=True, check=True,
                              env=hermetic_env(drop_git=True))  # fmt: skip
        return done.stdout.strip()

    def line(self, commits: list[Files], onto: str = "") -> str:
        """The tip of COMMITS, one after another, over ONTO (a root commit's without)."""
        self.n += 1
        out = []
        for i, files in enumerate(commits):
            out += [f"commit refs/heads/b{self.n}", f"mark :{i + 1}", "committer t <t@t> 1 +0000",
                    f"data {len(f'c{i}')}", f"c{i}"]  # fmt: skip
            out += [f"from {onto if not i else f':{i}'}"] if i or onto else []
            out += [f"M 100644 inline {p}\ndata {len(t)}\n{t}" for p, t in files.items()]
        self.git("fast-import", "--quiet", stdin="\n".join([*out, ""]))
        return self.git("rev-parse", f"refs/heads/b{self.n}")

    def unstamped(self, base: str, tip: str) -> list[str]:
        """The subjects `goh land --check` would gate again, of TIP's own commits over BASE."""
        self.git("update-ref", "--no-deref", "HEAD", tip)
        return [subject for _, subject, _ in commit_stamp.unstamped(self.path, base)]

    def stamp(self, base: str, tip: str) -> None:
        for sha in self.git("rev-list", f"{base}..{tip}").split():
            commit_stamp.record(commit_key.of(self.path, sha))


def _build_history(path: Path) -> Repo:
    fast_init(path, "main")
    return Repo(path)


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> Repo:
    """One object store for every test: each history is its own branch, each ledger its test's."""
    return _build_history(tmp_path_factory.mktemp("history") / "repo")


@pytest.fixture
def repo(history, tmp_path, monkeypatch) -> Repo:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("GOH_DB", raising=False)
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)
    return history


def test_eighteen_commits_rebased_over_prose_are_gated_by_nobody_again(repo) -> None:
    base = repo.line([BASE])
    lane = repo.line(LANE, base)
    assert len(repo.unstamped(base, lane)) == 18, "a commit no gate judged is owed one"
    repo.stamp(base, lane)
    main = repo.line([PROSE], base)
    repo.git("update-ref", "--no-deref", "HEAD", repo.line(LANE, main))
    assert not commit_stamp.check(
        repo.path, pytest.fail, ["false"], base=main
    )  # no gate, no worktree


def test_a_rebase_over_a_file_one_commit_touches_gates_that_commit_alone(repo) -> None:
    base = repo.line([BASE])
    lane = repo.line(LANE, base)
    repo.stamp(base, lane)
    main = repo.line([{"changelog/GOH.SHARED.md": "* z\n* a\n"}], base)
    moved = [*LANE[:5], {**LANE[5], "changelog/GOH.SHARED.md": "* z\n* a\n* mine\n"}, *LANE[6:]]
    assert repo.unstamped(main, repo.line(moved, main)) == ["c5"]


MINE = {"lib/mine.py": "one\ntwo\nTHREE\n"}
PLANTS: dict[str, tuple[Files, Files]] = {  # what moved on main, the commit as rebased over it
    "a changed gate": ({"gates/commit_gate.sh": "another gate\n"}, MINE),
    "a changed lib file": ({"lib/other.py": "A = 2\n"}, MINE),
    "a changed .gatesrc": ({".gatesrc": "GOH_MAX_LINES=400\n"}, MINE),
    "a seed a guard reads": ({"tests/wall_clock_seed.json": '{"a": 1}\n'}, MINE),
    "a seed a step reads": ({"run_paths_seed.json": '{"a": 1}\n'}, MINE),
    "a test a guard's scan reads": ({"tests/test_other.py": "def test_b(): pass\n"}, MINE),
    "a new file of no known kind": ({"new.bin": "x\n"}, MINE),
    "a decision": ({"docs/decisions/D-0001.md": "re-ruled\n"}, MINE),
    "a key's declaration": ({"docs/config/GOH_X.md": "another\n"}, MINE),
    "its touched file moved": (
        {"lib/mine.py": "ONE\ntwo\nthree\n"},
        {"lib/mine.py": "ONE\ntwo\nTHREE\n"},
    ),
    "a resolved conflict": (PROSE, {"lib/mine.py": "one\ntwo\nthree and main's\n"}),
}


@pytest.mark.parametrize("plant", PLANTS)
def test_what_the_gate_reads_moving_gates_the_commit_again(repo, plant: str) -> None:
    moved, after = PLANTS[plant]
    base = repo.line([BASE])
    lane = repo.line([MINE], base)
    repo.stamp(base, lane)
    main = repo.line([moved], base)
    assert repo.unstamped(main, repo.line([after], main)) == ["c0"], plant
    assert repo.unstamped(base, lane) == [], "where it was judged it is still judged"


def test_a_change_no_gate_saw_is_not_stamped_by_the_tree_it_ends_in(repo) -> None:
    """A commit made by --no-verify, squashed into (or amended by) one a gate judged: the tree is
    the judged one's, the change is not."""
    base = repo.line([BASE])
    red, judged = {"lib/red.py": "PLANTED\n"}, {"lib/green.py": "G = 1\n"}
    lane = repo.line([red, judged], base)
    repo.stamp(f"{lane}^", lane)
    assert repo.unstamped(base, lane) == ["c0"]
    squashed = repo.line([{**red, **judged}], base)
    assert repo.git("rev-parse", f"{squashed}^{{tree}}") == repo.git(
        "rev-parse", f"{lane}^{{tree}}"
    )
    assert repo.unstamped(base, squashed) == ["c0"]


def test_one_tree_made_by_another_change_to_the_same_file_is_another_key(repo) -> None:
    base = repo.line([BASE])
    lane = repo.line([{"lib/mine.py": "PLANTED\n"}, MINE], base)
    repo.stamp(f"{lane}^", lane)  # judged: PLANTED to MINE
    assert repo.unstamped(base, repo.line([MINE], base)) == ["c0"], "the base's to MINE was not"


def test_a_stamp_from_before_the_key_stamps_nothing(repo) -> None:
    base = repo.line([BASE])
    lane = repo.line([MINE], base)
    commit_stamp.record(repo.git("rev-parse", f"{lane}^{{tree}}"))
    assert repo.unstamped(base, lane) == ["c0"]


UNPLAIN: dict[str, Files] = {
    "a claim": {"lib/mine.py": "# `12` files in `docs/*.md`\n"},
    "a marker claim": {"lib/mine.py": "# claim: 12 files in docs\n"},
    "a fragment's link": {"changelog/GOH.NEW.md": "* [c](../docs/contracts.md)\n"},
    "a document": {"docs/map.md": "[c](contracts.md)\n"},
    "a seed": {"run_paths_seed.json": '{"b": 2}\n'},
    "the roadmap's own": {"docs/BACKLOG.md": "- **A** Done when: b\n"},
}


@pytest.mark.parametrize("kind", UNPLAIN)
def test_a_commit_that_can_read_prose_is_keyed_on_all_of_it(repo, kind: str) -> None:
    base = repo.line([BASE])
    repo.stamp(base, repo.line([UNPLAIN[kind]], base))
    main = repo.line([PROSE], base)
    assert repo.unstamped(main, repo.line([UNPLAIN[kind]], main)) == ["c0"], kind


def test_another_repositorys_commit_is_keyed_on_its_whole_tree(repo) -> None:
    """Its `tools/gate.sh` may read what it likes."""
    theirs = {p: t for p, t in BASE.items() if p != "gates/commit_gate.sh"}
    base = repo.line([theirs])
    repo.stamp(base, repo.line([MINE], base))
    main = repo.line([PROSE], base)
    assert repo.unstamped(main, repo.line([MINE], main)) == ["c0"]


def test_a_key_that_cannot_be_read_is_stamped_by_nothing(repo, monkeypatch) -> None:
    def unread(*_a: object) -> str:
        raise RuntimeError("git ls-tree: no object")

    base = repo.line([BASE])
    lane = repo.line([MINE], base)
    repo.stamp(base, lane)
    monkeypatch.setattr(commit_stamp.commit_key, "listing", unread)
    commit_stamp.record("")
    assert repo.unstamped(base, lane) == ["c0"]


def test_the_hook_stamps_the_key_the_check_asks_for(repo) -> None:
    """`commit_stamp.py record` at the hook (the index over HEAD) and `of` the commit it makes."""
    base = repo.line([BASE])
    lane = repo.line([MINE], base)
    repo.git("update-ref", "--no-deref", "HEAD", base)
    repo.git("read-tree", lane)
    assert commit_stamp.main(["record", str(repo.path)]) == 0
    assert repo.unstamped(base, lane) == []


# -- what UNREAD claims, held against the real tree and the real binary ----------------------------


def test_every_decision_is_found_outside_the_prose_no_key_holds() -> None:
    """`every decision record is whole` greps the whole index for a record's symbols: one found in
    unread prose alone would be a read the key does not hold."""
    where = [f":(glob)**/{p}" if p.startswith("*") else p for p in SOURCES]
    lost, read = [], 0
    for path in sorted((REPO_ROOT / "docs" / "decisions").glob("D-*.md")):
        lives = re.search(r"\*\*Lives in:\*\*(.*?)(?=\n- \*\*|\n#|\Z)", path.read_text(), re.DOTALL)
        for token in re.findall(r"`([^`]+)`", lives.group(1) if lives else ""):
            word, read = token.split()[0], read + 1
            if "/" in token or "." in token:
                if commit_key.UNREAD.fullmatch(token):
                    lost.append(f"{path.name}: {token}")
                continue
            found = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "grep", "-q", "-w", "-F", "-e", word, "--",
                 *where], stdin=subprocess.DEVNULL, check=False)  # fmt: skip
            if found.returncode:
                lost.append(f"{path.name}: {word}")
    assert read > 20, "the records were not read"
    assert not lost, f"found only in prose a commit's key leaves out: {lost}"


SHIM = """#!/bin/sh
printf '%s\\n' "$*" >> "$GIT_READS"
case " $* " in
*" cat-file --batch"*) tee -a "$GIT_READS" | exec "{git}" "$@" ;;
*) exec "{git}" "$@" ;;
esac
"""
READ = re.compile(r"^(?:.* )?:(\S+)$")  # `show :path`, and a batch's `:path` line


def _staged_reads(
    goh: Path, top: Path, tmp_path: Path, edit: Callable[[Files], Files]
) -> tuple[set, set]:
    """(paths whose index blob a real `goh structural --staged` read, the staged paths) of this
    tree's HEAD with EDIT's files staged: a private index and object store, no work tree."""
    real = subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=True).stdout.strip()  # fmt: skip
    shim, log = tmp_path / "shim", tmp_path / "reads"
    shim.mkdir()
    (shim / "git").write_text(SHIM.format(git=real))
    (shim / "git").chmod(0o755)
    env = hermetic_env(drop_git=True)

    def git(*words: str, stdin: str = "") -> str:
        return subprocess.run([real, "-C", str(top), *words], env=env, input=stdin, text=True,
                              capture_output=True, check=True).stdout.strip()  # fmt: skip

    at = subprocess.run([real, "-C", str(REPO_ROOT), "rev-parse", "HEAD", "--git-path", "objects"],
                        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
                        check=True).stdout.split()  # fmt: skip
    store = (REPO_ROOT / at[1]).resolve()
    (top / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (top / ".git" / "objects" / "info" / "alternates").write_text(f"{store}\n")
    git("update-ref", "HEAD", at[0])
    git("read-tree", "HEAD")
    # most of the unreaped scan's subject emptied: its debug build takes 5 s over all of it
    empty = git("hash-object", "-w", "--stdin")
    bulk = git("ls-files", "--", "tests/test_*.py").split()
    kept = {"tests/test_release_kit_update_dev.py", "tests/test_self_match_wait.py"}  # allowlisted
    git("update-index", "--index-info",
        stdin="".join(f"100644 {empty}\t{p}\n" for p in bulk if p not in kept))  # fmt: skip
    stub = git("hash-object", "-w", "--stdin", stdin="# Changelog\n\n## [Unreleased]\n")
    git("update-index", "--cacheinfo", f"100644,{stub},CHANGELOG.md")  # never the release's
    git("update-ref", "HEAD", git("commit-tree", "-p", "HEAD", "-m", "x", git("write-tree")))
    names = ("lib/commit_key.py", "gates/staged_guards.sh", "crates/goh/src/blobs.rs")
    was = {p: (REPO_ROOT / p).read_text(encoding="utf-8") for p in names}
    staged = edit(was)
    for path, text in staged.items():
        blob = git("hash-object", "-w", "--stdin", stdin=text)
        git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    allowed = {
        e["path"] for e in json.loads(git("show", "HEAD:kill_by_name_allow.json"))["entries"]
    }
    for disk in (".gatesrc", "pyproject.toml", *allowed):  # read from the work tree
        (top / disk).parent.mkdir(parents=True, exist_ok=True)
        (top / disk).write_text(git("show", f"HEAD:{disk}") + "\n")
    env |= {"PATH": f"{shim}:{env['PATH']}", "GIT_READS": str(log)}
    done = subprocess.run([str(goh), "structural", "--staged"], cwd=top, env=env, text=True,
                          capture_output=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr  # red, it stopped short of a step
    found = {m.group(1) for m in map(READ.match, log.read_text().splitlines()) if m}
    return found, set(staged)


def test_the_binarys_staged_run_reads_no_prose_the_key_leaves_out(
    goh: Path, history, tmp_path
) -> None:
    """Calibrated by what it does read: the unreaped scan's other tests, the decisions, a seed."""
    read, staged = _staged_reads(goh, history.path, tmp_path, lambda was: {
        **{p: f"{t}\n\n# a comment\n" if not p.endswith(".rs") else f"{t}\n// a comment\n"
           for p, t in was.items()},
        "changelog/GOH.PLANTED.md": "* GOH.PLANTED: a line.\n",
        "tests/test_planted.py": "def test_a():\n    pass\n",
    })  # fmt: skip
    assert staged <= read, "the staged blobs were not read: the shim is blind"
    beyond = read - staged
    assert {"lib/reuse.py", "tests/conftest.py"} & beyond and len(beyond) > 500, len(beyond)
    assert any(p.startswith("docs/decisions/") for p in beyond), "the decisions step did not run"
    assert "kill_by_name_allow.json" in beyond, "the seeded steps did not run"
    assert not [p for p in beyond if commit_key.UNREAD.fullmatch(p)]
