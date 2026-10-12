"""The release kit refuses to tag over a red, pending or missing CI run on HEAD (BACKLOG 6.5).

ztools' CI has been red since 2026-10-10 (`.python-version` is gitignored and ci.yml reads it),
and v3.6.0, v3.7.0 and v3.8.0 were each tagged over it. The kit's first step now reads the runs
for HEAD (`gh run list --commit`); the fake gh lists what a test plants in runs.tsv. A repo with
no workflows says "no CI", which is not a green.
"""

from __future__ import annotations

from test_release_kit import TAG, commit_all, kit, run_release  # noqa: F401  (kit: a fixture)

RED = "completed\tfailure\tci\thttps://example.invalid/runs/1\n"
PENDING = "in_progress\t\tci\thttps://example.invalid/runs/2\n"
GREEN = "completed\tsuccess\tci\thttps://example.invalid/runs/3\ncompleted\tskipped\tdocs\thttps://example.invalid/runs/4\n"


def _with_ci(kit: dict, runs: str | None) -> dict:
    wf = kit["proj"] / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\njobs: {}\n")
    commit_all(kit["proj"], "ci")
    if runs is not None:
        (kit["gh_state"] / "runs.tsv").write_text(runs)
    return kit


def _tagged(kit: dict) -> bool:
    return (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()


def test_a_red_run_refuses_the_tag_and_names_the_run(kit):
    r = run_release(_with_ci(kit, RED))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "'ci'" in out, out
    assert "ci: failure" in out and "runs/1" in out, out
    assert not _tagged(kit)


def test_a_pending_run_refuses_the_tag(kit):
    r = run_release(_with_ci(kit, PENDING))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "ci: in_progress" in out, out
    assert not _tagged(kit)


def test_no_run_for_head_refuses_the_tag(kit):
    r = run_release(_with_ci(kit, None))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "no CI run for HEAD" in out, out
    assert not _tagged(kit)


def test_every_run_green_tags(kit):
    r = run_release(_with_ci(kit, GREEN))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "2 CI run(s)" in out, out
    assert _tagged(kit)


def test_a_repo_without_workflows_says_no_ci_and_tags(kit):
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "no CI:" in out and "not a green" in out, out


def test_the_skip_is_said_aloud(kit):
    r = run_release(_with_ci(kit, RED), "--no-ci-check")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "skipped (--no-ci-check)" in out, out
