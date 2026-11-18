"""gates/diff_cov.py -- the executable Rust lines a change adds or edits are covered (GOH.DIFFCOV).

The whole-crate floor let a change add untested lines while the crate total stayed above it. This
reader judges the CHANGE: the lines the diff from the change's base adds or edits, mapped onto the
final lcov parts of the same run on their NEW side (a renamed or moved file is judged where its
lines are now), each with a line record (executable) and a count of 0 is red, named by file and
line. Unreachable or other-platform lines go on a reasoned allowlist whose stale entries are red.
Synthetic lcov parts over a real git repository: no cargo here (tests/test_diff_cov_rust.py runs
the real instrumented build once).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env
from timing_bounds import patience

DIFF_COV = REPO_ROOT / "gates" / "diff_cov.py"
BASE = "pub fn a() -> u32 {\n    1\n}\n"
# Lines 4-6 are added: `pub fn b`, its body, its close.
ADDED = BASE + "pub fn b() -> u32 {\n    2\n}\n"


def _run(repo: Path, parts: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DIFF_COV), "--repo", str(repo), *args, str(parts)],
        cwd=repo, capture_output=True, text=True, env=hermetic_env(**env), timeout=patience(60),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def _part(parts: Path, records: dict[Path, dict[int, int]], name: str = "part-workspace.info"):
    parts.mkdir(parents=True, exist_ok=True)
    text = ""
    for f, das in records.items():
        text += f"SF:{f}\n" + "".join(f"DA:{ln},{n}\n" for ln, n in sorted(das.items()))
        text += "end_of_record\n"
    (parts / name).write_text(text, encoding="utf-8")


def _crate(repo: Path, body: str = BASE, rel: str = "src/lib.rs") -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "lane")


def test_an_added_uncovered_line_is_red_naming_the_file_and_line(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 3, 2: 3, 4: 0, 5: 0}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "src/lib.rs:4" in out and "src/lib.rs:5" in out, out
    assert "2 of 2" in out, out


def test_added_covered_lines_are_green(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 3, 2: 3, 4: 1, 5: 1}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2/2" in r.stdout, r.stdout


def test_an_unchanged_uncovered_line_is_the_floors_not_this_checks(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 0, 2: 0, 4: 1, 5: 1}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_edited_line_is_judged_like_an_added_one(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, BASE.replace("    1\n", "    7\n"))
    commit_all(repo, "edit a")
    _part(tmp_path / "p", {lib: {1: 1, 2: 0}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1 and "src/lib.rs:2" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_moved_file_is_judged_on_its_new_side(repo: Path, tmp_path: Path):
    """A rename with lines inserted above: the uncovered line is named at its NEW path and number,
    and the renamed file's unchanged lines are not the change."""
    _crate(repo, BASE * 4)
    commit_all(repo, "base")
    _branch(repo)
    git(repo, "mv", "src/lib.rs", "src/moved.rs")
    moved = repo / "src" / "moved.rs"
    moved.write_text("pub fn z() -> u32 {\n    9\n}\n" + BASE * 4, encoding="utf-8")
    commit_all(repo, "move and add z")
    # New side: z is lines 1-3, the old content 4-15, uncovered everywhere but z's body.
    das = dict.fromkeys(range(4, 16), 0)
    das.update({1: 1, 2: 0})
    _part(tmp_path / "p", {moved: das})
    r = _run(repo, tmp_path / "p")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "src/moved.rs:2" in out, out
    assert "1 of 2" in out, out  # lines 1 and 2 are executable and changed; 4-15 are not changed


def test_a_land_judges_everything_since_the_merge_base_with_main(repo: Path, tmp_path: Path):
    """Two commits on the branch, the uncovered line in the FIRST: HEAD's parent would miss it."""
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    (repo / "README.md").write_text("# moved on\n", encoding="utf-8")
    commit_all(repo, "docs")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1 and "src/lib.rs:5" in r.stdout + r.stderr, r.stdout + r.stderr
    assert "main" in r.stdout + r.stderr  # the base is named, and where it came from


def test_a_push_judges_from_the_change_base_it_is_given(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    head = git(repo, "rev-parse", "HEAD").strip()
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    # The remote already holds HEAD: nothing is pushed, nothing is judged.
    assert _run(repo, tmp_path / "p", GOH_CHANGE_BASE=head).returncode == 0
    # The remote holds main: the added line is pushed, and red.
    main = git(repo, "rev-parse", "main").strip()
    assert _run(repo, tmp_path / "p", GOH_CHANGE_BASE=main).returncode == 1


def test_a_consumer_push_judges_from_its_upstream(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "remote", "add", "origin", str(tmp_path / "nowhere.git"))
    git(repo, "config", "branch.main.remote", "origin")
    git(repo, "config", "branch.main.merge", "refs/heads/main")
    (repo / "README.md").write_text("# local\n", encoding="utf-8")
    commit_all(repo, "docs")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr  # line 5 is already upstream
    assert "upstream" in r.stdout, r.stdout


def _allow(repo: Path, *entries: dict) -> None:
    (repo / "diffcov_allow.json").write_text(json.dumps({"entries": list(entries)}))


def test_a_reasoned_allowlist_entry_exempts_its_line(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    _allow(repo, {"file": "src/lib.rs", "text": "2", "reason": "unreachable: fixture"})
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "allowlisted" in r.stdout, r.stdout


def test_a_stale_allowlist_entry_is_red(repo: Path, tmp_path: Path):
    """The ratchet: an entry whose line is now covered, or gone from its file, must be deleted."""
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 1}})
    _allow(repo, {"file": "src/lib.rs", "text": "2", "reason": "unreachable: fixture"})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1 and "stale" in r.stdout + r.stderr, r.stdout + r.stderr
    _allow(repo, {"file": "src/lib.rs", "text": "never_here()", "reason": "gone"})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1 and "stale" in r.stdout + r.stderr, r.stdout + r.stderr
    _allow(repo, {"file": "src/gone.rs", "text": "2", "reason": "gone"})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 1 and "stale" in r.stdout + r.stderr, r.stdout + r.stderr


def test_an_entry_not_executable_on_this_platform_is_kept(repo: Path, tmp_path: Path):
    """No line record here (cfg'd out of this platform's build): another platform's report needs
    it, so it is neither stale nor counted."""
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1}})
    _allow(repo, {"file": "src/lib.rs", "text": "2", "reason": "linux-only arm"})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_entry_without_a_reason_is_refused(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _part(tmp_path / "p", {lib: {1: 1}})
    _allow(repo, {"file": "src/lib.rs", "text": "2", "reason": " "})
    r = _run(repo, tmp_path / "p")
    assert r.returncode == 2 and "reason" in r.stderr, r.stdout + r.stderr


def test_the_knob(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    r = _run(repo, tmp_path / "p", GOH_COV_DIFF="warn")
    assert r.returncode == 0 and "src/lib.rs:5" in r.stdout + r.stderr, r.stdout + r.stderr
    r = _run(repo, tmp_path / "p", GOH_COV_DIFF="off")
    assert r.returncode == 0 and "off" in r.stdout, r.stdout
    r = _run(repo, tmp_path / "p", GOH_COV_DIFF="maybe")
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_include_filter_scopes_it_like_the_floor(repo: Path, tmp_path: Path):
    lib = _crate(repo)
    commit_all(repo, "base")
    _branch(repo)
    _crate(repo, ADDED)
    commit_all(repo, "add b")
    _part(tmp_path / "p", {lib: {1: 1, 2: 1, 4: 1, 5: 0}})
    assert _run(repo, tmp_path / "p", "--include", "other/").returncode == 0
    assert _run(repo, tmp_path / "p", GOH_COV_INCLUDE_RE="src/").returncode == 1


def test_outside_git_nothing_is_a_change(tmp_path: Path):
    crate = tmp_path / "c"
    lib = _crate(crate)
    _part(tmp_path / "p", {lib: {1: 0}})
    r = _run(crate, tmp_path / "p")
    assert r.returncode == 0 and "not a git checkout" in r.stdout, r.stdout + r.stderr
