"""What moved while a proven step ran: the line names it (proven.sh and local_ci.sh).

Split from test_proven.py at the 500-line cap; its fixtures are reused, not copied.
"""

import test_proven as _tp
from test_proven import _git, local_ci, proven, records

# The fixtures, shared by assignment (pytest collects them by name from this module).
goh, repo, env, hooked = _tp.goh, _tp.repo, _tp.env, _tp.hooked


def test_a_kept_file_the_step_rewrote_is_named_not_called_the_tree(goh, repo, env):
    # divoom-control, 2026-11-01: six pre-push steps said "the tree or the gates moved" on a tree
    # nobody touched. Each had rebuilt target/debug/divoomd, which GOH_EXPORT_KEEP names: the key
    # reads the kept file's content, so a step that rewrites it moves its own key. Not recording
    # is right; blaming the tree or the gates sent the reader to the wrong place.
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("before\n")
    keep = dict(env, GOH_EXPORT_KEEP="build/out.bin")
    step = 'echo ran >> "$COUNT"; echo rebuilt > build/out.bin'
    r = proven(goh, repo, keep, step)
    # Red since GOH.KEEP1: an unlisted rewrite of a kept path is a new instance of the class
    # (tests/test_kept_rewrite.py); the "what moved" line is still printed first.
    assert r.returncode == 1, r.stdout + r.stderr
    assert "moved while the step ran" in r.stdout
    assert "kept file build/out.bin" in r.stdout, r.stdout
    assert "the tree (" not in r.stdout and "the tree or the gates" not in r.stdout, r.stdout
    assert records(repo) == []


def test_local_ci_names_the_kept_file_a_step_rewrote(goh, hooked, env):
    """The push gate's runner says what moved, the same way proven.sh does (one helper)."""
    (hooked / "build").mkdir()
    (hooked / "build" / "out.bin").write_text("before\n")
    (hooked / ".gatesrc").write_text(
        "GOH_EXPORT_KEEP='build/out.bin'\n"
        "GOH_CI_STEPS='echo ran >> \"$COUNT\"; echo rebuilt > build/out.bin'\n"
    )
    _git(hooked, "add", "-A")
    _git(hooked, "commit", "-q", "-m", "keep", "--no-verify")
    r = local_ci(goh, hooked, dict(env, GOH_EXPORT_KEEP="build/out.bin"))
    assert r.returncode == 1, r.stdout + r.stderr  # red since GOH.KEEP1 (test_kept_rewrite.py)
    assert "kept file build/out.bin" in r.stdout and "moved while the step ran" in r.stdout, (
        r.stdout
    )
