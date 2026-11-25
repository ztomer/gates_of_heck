"""Every refusing check of the release kit runs before its first commit or push (GOH.KITORDER).

ztools 3.19.0 (ZT.MTIMECLASS, 2026-11-11): release.sh pushed the unpushed "chore: version" commit
for its CI read (step 0), then refused at step 2 because CHANGELOG.md held no stanza for the version
-- a pushed, untagged bump on origin, and the stanza had to follow in a commit of its own. A gate
that refuses must refuse before anything irreversible happens, so each refusal the kit can decide
from the tree (the stanza, the changelog check, a stale tag, no gate declared) is asked before step
0b's cut commit and step 0's push. Each test here commits an unpushed version commit, as the
consumers' wrappers do, and asserts that the refused run left origin and HEAD where they were.
"""

from __future__ import annotations

import test_release_kit as rk
from test_release_kit import STANZA_BODY, TAG, VERSION, commit_all, run_release, sh

kit = rk.kit  # the release fixture, by assignment (an import would be shadowed by each test's arg)


GREEN = "completed\tsuccess\tci\thttps://example.invalid/runs/3\n"


def _with_ci(kit: dict) -> None:
    """CI workflows, as every consumer has: the kit's step 0 pushes an unpushed HEAD for its runs,
    which is the push a refusal used to follow. Committed and pushed, so origin holds them."""
    wf = kit["proj"] / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\njobs: {}\n")
    commit_all(kit["proj"], "ci")
    sh(kit["proj"], "push", "-q", "origin", "HEAD:refs/heads/main")
    (kit["gh_state"] / "runs.tsv").write_text(GREEN)


def _bump(kit: dict, changelog: str) -> tuple[str, str]:
    """A wrapper's unpushed `chore: version` commit with CHANGELOG; (local HEAD, origin main)."""
    _with_ci(kit)
    (kit["proj"] / "CHANGELOG.md").write_text(changelog)
    commit_all(kit["proj"], f"chore: version {VERSION}")
    return sh(kit["proj"], "rev-parse", "HEAD"), sh(kit["origin"], "rev-parse", "main")


def _untouched(kit: dict, head: str, origin: str) -> None:
    assert sh(kit["proj"], "rev-parse", "HEAD") == head, "the refused run committed"
    assert sh(kit["origin"], "rev-parse", "main") == origin, "the refused run pushed"
    assert not sh(kit["proj"], "tag", "-l", TAG).strip(), "the refused run tagged"


def test_no_stanza_and_nothing_to_cut_refuses_before_any_push(kit):
    head, origin = _bump(kit, "# C\n\n## Unreleased\n\n## v1.1.0\n\n- older\n")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "step 'changelog' failed" in out and VERSION in out, out
    _untouched(kit, head, origin)


def test_no_changelog_at_all_refuses_before_any_push(kit):
    _with_ci(kit)
    (kit["proj"] / "CHANGELOG.md").unlink()
    commit_all(kit["proj"], f"chore: version {VERSION}")
    head, origin = sh(kit["proj"], "rev-parse", "HEAD"), sh(kit["origin"], "rev-parse", "main")
    r = run_release(kit)
    assert r.returncode != 0 and "step 'changelog' failed" in r.stderr, r.stdout + r.stderr
    _untouched(kit, head, origin)


def test_a_stanza_the_check_refuses_is_refused_before_any_push(kit):
    same = f"# C\n\n## {TAG}\n\n* the same\n  entry\n\n## v1.1.0\n\n* the  same entry\n"
    head, origin = _bump(kit, same)
    r = run_release(kit)
    assert r.returncode != 0 and "duplicate entry" in r.stderr, r.stdout + r.stderr
    assert "v1.1.0" in r.stderr, r.stderr
    _untouched(kit, head, origin)


def test_a_stale_tag_is_refused_before_any_push(kit):
    sh(kit["proj"], "tag", "-a", TAG, "-m", "old")  # at the fixture commit, behind HEAD below
    head, origin = _bump(kit, f"# C\n\n## {TAG}\n\n{STANZA_BODY}\n\n## v1.1.0\n\n- older\n")
    r = run_release(kit)
    assert r.returncode != 0 and "step 'tag' failed" in r.stderr, r.stdout + r.stderr
    assert sh(kit["proj"], "rev-parse", "HEAD") == head
    assert sh(kit["origin"], "rev-parse", "main") == origin


def test_no_gate_declared_is_refused_before_any_push(kit):
    head, origin = _bump(kit, f"# C\n\n## {TAG}\n\n{STANZA_BODY}\n\n## v1.1.0\n\n- older\n")
    r = run_release(kit, gate=None)
    assert r.returncode != 0 and "no gate configured" in r.stderr, r.stdout + r.stderr
    _untouched(kit, head, origin)


def test_a_cut_is_checked_before_it_is_committed_and_the_release_still_lands(kit):
    """The passing control: entries under Unreleased are cut, committed, pushed and tagged."""
    _bump(kit, "# C\n\n## Unreleased\n\n* the new thing\n\n## v1.1.0\n\n- older\n")
    r = run_release(kit)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sh(kit["origin"], "rev-parse", "main") == sh(kit["proj"], "rev-parse", "HEAD")
    assert TAG in sh(kit["origin"], "tag", "-l")
