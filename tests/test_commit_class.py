"""`goh commit-class`: a fix names the CLASS it belongs to, and a class seen twice before is ended.

Ported from ZoneWM's `tools/check_commit_class.py` (roadmap Phase 8; its selftest cases are the
first block below). One deliberate change, at ZoneWM's suggestion: a new commit's trailers must be
in git's own trailer block -- the message's LAST paragraph -- so `git log --format=%(trailers)`
and every other tool reads what the gate read. History is read the old way too (a `Class:` line
anywhere), so the classes committed before the port still count toward a third instance.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env, native_goh_path

OK_FIX = (
    "fix(x): y\n\nbody\n\n"
    "Class: a lock path declared in several files\n"
    "Siblings: none (grep -rn zonetiler-qa.lock tools)\n"
)
TWICE = ("lock path declared twice", "the lock path declared in two files")


def _commit(repo: Path, msg: str) -> str:
    git(repo, "commit", "-q", "--allow-empty", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def _check(repo: Path, msg: str, *history: str) -> subprocess.CompletedProcess[str]:
    for cls in history:  # the pre-port form: a Class line in a paragraph above the trailer block
        _commit(repo, f"fix: earlier\n\nClass: {cls}\nSiblings: a, b\n\nCo-Authored-By: x <x@x>")
    path = repo.parent / "MSG"
    path.write_text(msg)
    return subprocess.run(
        [str(native_goh_path()), "commit-class", str(path)], cwd=repo, capture_output=True,
        text=True, env=hermetic_env(drop_git=True),
    )  # fmt: skip


def _refused(r: subprocess.CompletedProcess[str], why: str) -> bool:
    return r.returncode == 1 and why in r.stderr


def test_a_docs_commit_needs_no_trailers(repo: Path) -> None:
    assert _check(repo, "docs: z\n").returncode == 0


def test_a_fix_without_a_class_is_refused(repo: Path) -> None:
    assert _refused(_check(repo, "fix: y\n\nSiblings: a, b\n"), "Class")


def test_a_fix_without_siblings_is_refused(repo: Path) -> None:
    assert _refused(_check(repo, "fix: y\n\nClass: c d e\n"), "Siblings")


def test_a_bare_none_is_refused_and_a_named_search_passes(repo: Path) -> None:
    assert _refused(_check(repo, "fix: y\n\nClass: c d e\nSiblings: none\n"), "how it knows")
    assert (
        _check(repo, "fix: y\n\nClass: c d e\nSiblings: none (rg -n lock tools)\n").returncode == 0
    )


def test_a_fix_with_both_trailers_passes(repo: Path) -> None:
    r = _check(repo, OK_FIX)
    assert r.returncode == 0, r.stderr


def test_perf_and_a_scoped_breaking_fix_are_gated_too(repo: Path) -> None:
    assert _check(repo, "perf(b): q\n").returncode == 1
    assert _check(repo, "fix(core)!: q\n").returncode == 1
    assert _check(repo, "fixup! fix: q\n").returncode == 0  # an autosquash commit is not a fix


def test_a_third_instance_of_a_class_is_refused(repo: Path) -> None:
    assert _refused(_check(repo, OK_FIX, *TWICE), "earlier ones")


def test_a_third_instance_that_ends_the_class_passes(repo: Path) -> None:
    r = _check(repo, OK_FIX + "Systemic: tools/qa_lock.py declares it once\n", *TWICE)
    assert r.returncode == 0, r.stderr


def test_a_third_instance_that_says_it_did_not_end_the_class_is_refused(repo: Path) -> None:
    for word in ("none yet; a later commit will", "n/a", "tbd", "- later"):
        r = _check(repo, OK_FIX + f"Filed: {word}\n", *TWICE)
        assert _refused(r, "earlier ones"), word
        git(repo, "reset", "-q", "--hard", "HEAD~2")


def test_one_earlier_instance_is_not_yet_a_class(repo: Path) -> None:
    assert _check(repo, OK_FIX, TWICE[0]).returncode == 0


def test_unrelated_classes_do_not_count(repo: Path) -> None:
    assert _check(repo, OK_FIX, "window built per show", "timer never idles").returncode == 0


def test_trailers_above_the_last_paragraph_are_named_not_read(repo: Path) -> None:
    """git reads trailers only in the last paragraph; `Class:` above a Co-Authored-By block is
    invisible to `%(trailers)`. Refused, saying where to move it."""
    msg = OK_FIX + "\nCo-Authored-By: x <x@x>\n"
    r = _check(repo, msg)
    assert _refused(r, "last paragraph"), r.stderr
    fixed = OK_FIX.replace("Siblings:", "Co-Authored-By: x <x@x>\nSiblings:")
    assert _check(repo, fixed).returncode == 0
    out = git(repo, "interpret-trailers", "--parse", str(repo.parent / "MSG"))
    assert "Class: a lock path" in out, "the passing form must be git's own trailer block"


def test_the_editor_buffer_comments_and_scissors_are_ignored(repo: Path) -> None:
    msg = (
        OK_FIX
        + "# Please enter the commit message\n# ------------------------ >8 ------------------------\ndiff --git a/x b/x\nSiblings:\n"
    )
    assert _check(repo, msg).returncode == 0


def _range(repo: Path, rev: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(native_goh_path()), "commit-class", "--range", rev], cwd=repo, capture_output=True,
        text=True, env=hermetic_env(drop_git=True),
    )  # fmt: skip


def test_a_range_names_each_bad_commit_and_counts_classes_within_it(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, OK_FIX.replace("several files", "two places"))
    bad = _commit(repo, "fix: no trailers at all")
    _commit(repo, OK_FIX.replace("several files", "three places"))
    third = _commit(repo, OK_FIX)  # the third lock-path class, inside the range itself
    r = _range(repo, f"{base}..HEAD")
    assert r.returncode == 1
    assert bad[:8] in r.stderr and third[:8] in r.stderr, r.stderr
    assert r.stderr.count("✗ ") >= 2


def test_a_range_with_every_fix_named_passes(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, OK_FIX)
    _commit(repo, "docs: nothing to say")
    assert _range(repo, f"{base}..HEAD").returncode == 0


def _hooked(repo: Path, gatesrc: str) -> None:
    subprocess.run(["bash", str(REPO_ROOT / "install.sh"), str(repo)], check=True,
                   capture_output=True, env=hermetic_env(drop_git=True, GOH_SKIP_BUILD="1"))  # fmt: skip
    (repo / ".gatesrc").write_text(gatesrc)


def _git_commit(repo: Path, msg: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT), GOH_BIN=str(native_goh_path()))
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                          capture_output=True, text=True, env=env)  # fmt: skip


def test_the_commit_msg_hook_runs_it_when_the_repo_opts_in(repo: Path) -> None:
    _hooked(repo, "GOH_COMMIT_CLASS=1\n")
    assert (repo / ".githooks" / "commit-msg").is_file()
    r = _git_commit(repo, "fix: y")
    assert r.returncode != 0 and "Class" in r.stderr, r.stderr
    assert _git_commit(repo, OK_FIX).returncode == 0


def test_the_commit_msg_hook_is_inert_without_the_key(repo: Path) -> None:
    _hooked(repo, "")
    r = _git_commit(repo, "fix: y")
    assert r.returncode == 0, r.stderr


def _push_over(tmp_path: Path, gatesrc: str) -> subprocess.CompletedProcess[str]:
    """A fix committed with --no-verify on top of a base the remote has, pushed through push_gate."""
    from test_push_gate import PUSH_GATE, _repo

    proj = _repo(tmp_path, gatesrc=gatesrc)
    base = git(proj, "rev-parse", "HEAD").strip()
    tip = _commit(proj, "fix: skipped the hook")
    env = hermetic_env(drop_git=True, GOH_DIR=str(REPO_ROOT), GOH_BIN=str(native_goh_path()),
                       GATE_REPORT=str(tmp_path / "report.txt"),
                       GOH_PUSH_LOGS=str(tmp_path / "push-logs"))  # fmt: skip
    return subprocess.run(
        ["bash", str(PUSH_GATE), "origin", "unused-url"], cwd=proj, env=env, text=True,
        input=f"refs/heads/main {tip} refs/heads/main {base}\n", capture_output=True,
    )  # fmt: skip


def test_the_push_asks_again_so_no_verify_does_not_survive_it(tmp_path: Path) -> None:
    r = _push_over(tmp_path, "GOH_COMMIT_CLASS=1\n")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "skipped the hook" in r.stderr and "nothing pushed" in r.stderr, r.stderr


def test_the_push_is_untouched_without_the_key(tmp_path: Path) -> None:
    r = _push_over(tmp_path, "")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "commit_class" not in r.stdout + r.stderr


def test_a_replay_judges_history_by_the_rule_it_was_written_under(repo: Path) -> None:
    """`--report` replays history: a pre-port commit's trailers above Co-Authored-By are read, so
    the replay shows the class rule's verdicts, not one placement line per old commit."""
    base = git(repo, "rev-parse", "HEAD").strip()
    _commit(repo, OK_FIX + "\nCo-Authored-By: x <x@x>\n")  # placement only: the pre-port form
    bad = _commit(repo, "fix: no trailers at all")
    r = subprocess.run(
        [str(native_goh_path()), "commit-class", "--report", "--range", f"{base}..HEAD"],
        cwd=repo, capture_output=True, text=True, env=hermetic_env(drop_git=True),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert bad[:8] in r.stderr and "last paragraph" not in r.stderr, r.stderr
    assert "2 commit(s), 1 the rule refuses" in r.stdout, r.stdout


def test_a_reworded_third_instance_is_still_the_class(repo: Path) -> None:
    """ZoneWM's own third "ceiling recorded" class (2026-10-08): written afresh each time, so the
    prototype's half-the-words rule saw three unrelated classes and refused none."""
    earlier = (
        "a ceiling recorded over a defect, which then guards the defect",
        "a ceiling recorded under conditions the gate never judges it in",
    )
    third = OK_FIX.replace(
        "a lock path declared in several files",
        "a ceiling recorded before a fix still carries the worst round the fix removed",
    )
    assert _refused(_check(repo, third, *earlier), "earlier ones")
