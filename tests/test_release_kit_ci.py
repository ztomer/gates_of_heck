"""The release kit refuses to tag over a red CI run on HEAD (BACKLOG 6.5), and reads CI once, never
waiting: a pending or missing run is ADVISORY over goh's push gate (GOH.CIWAIT, owner 2026-11-11).

ztools' CI has been red since 2026-10-10 (`.python-version` is gitignored and ci.yml reads it),
and v3.6.0, v3.7.0 and v3.8.0 were each tagged over it. The kit's first step now reads the runs
for HEAD (`gh run list --commit`); the fake gh lists what a test plants in runs.tsv. A repo with
no workflows says "no CI", which is not a green.
"""

from __future__ import annotations

import subprocess

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


def _push_gated(kit: dict) -> dict:
    """The checkout's pre-push hook is goh's push gate: the local record that a commit passed the
    full gate before it could be pushed (what .loop's `loopctl done` reads the same way)."""
    hook = kit["proj"] / ".git" / "hooks" / "pre-push"
    hook.write_text(
        '#!/bin/sh\n# exec "$GOH_DIR/gates/push_gate.sh" (a stand-in that passes)\nexit 0\n'
    )
    hook.chmod(0o755)
    return kit


def _run_lists(kit: dict) -> int:
    log = kit["gh_log"]
    return sum("run list" in ln for ln in log.read_text().splitlines()) if log.exists() else 0


def test_a_red_run_refuses_the_tag_and_names_the_run(kit):
    r = run_release(_with_ci(kit, RED))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "'ci'" in out, out
    assert "ci: failure" in out and "runs/1" in out, out
    assert not _tagged(kit)


def test_a_pending_run_without_the_push_gate_refuses_at_once(kit):
    r = run_release(_with_ci(kit, PENDING))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "ci: in_progress" in out and "push gate" in out, out
    assert _run_lists(kit) == 1, "the kit read CI more than once"
    assert not _tagged(kit)


def test_no_run_for_head_without_the_push_gate_refuses_the_tag(kit):
    r = run_release(_with_ci(kit, None))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "no CI run for HEAD" in out, out
    assert not _tagged(kit)


def test_a_pending_run_over_the_push_gate_tags_advisory_after_one_read(kit):
    """GOH.CIWAIT: the kit watched CI up to 1800 s for a run re-running the gate the push already
    passed. Over goh's push gate a pending run is advisory: tagged now, said, read once."""
    r = run_release(_push_gated(_with_ci(kit, PENDING)))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "advisory" in out and "ci: in_progress" in out, out
    assert _run_lists(kit) == 1, "the kit read CI more than once"
    assert _tagged(kit)


def test_no_run_yet_over_the_push_gate_tags_advisory(kit):
    r = run_release(_push_gated(_with_ci(kit, None)))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "advisory" in out, out
    assert _tagged(kit)


def test_a_red_run_over_the_push_gate_still_refuses(kit):
    r = run_release(_push_gated(_with_ci(kit, RED)))
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "ci: failure" in out, out
    assert not _tagged(kit)


def test_every_run_green_tags(kit):
    r = run_release(_with_ci(kit, GREEN))
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "2 CI runs" in out, out
    assert _tagged(kit)


def test_a_repo_without_workflows_says_no_ci_and_tags(kit):
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "no CI:" in out and "not a green" in out, out


def test_the_skip_is_said_aloud(kit):
    r = run_release(_with_ci(kit, RED), "--no-ci-check")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "skipped (--no-ci-check)" in out, out


# ── R3.13: Actions DISABLED is said, not waited on (sys_updater, 2026-10) ─────────────────────────
#
# sys_updater tracks workflows and has GitHub Actions disabled: no run ever comes, so the kit
# refused "no CI run for HEAD" and the release went out with --no-ci-check -- a skip that also
# skips every repo where the run is genuinely missing. The kit asks the API instead
# (`repos/OWNER/NAME/actions/permissions`), and a disabled repo is "no CI", which is not a green.


def test_actions_disabled_is_named_and_the_tag_is_made(kit):
    _with_ci(kit, None)
    (kit["gh_state"] / "actions_enabled").write_text("false\n")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "GitHub Actions is disabled" in out and "not a green" in out, out
    assert "no CI run for HEAD" not in out, out
    assert _tagged(kit)


def test_actions_enabled_with_no_run_still_refuses(kit):
    _with_ci(kit, None)
    (kit["gh_state"] / "actions_enabled").write_text("true\n")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "no CI run for HEAD" in out, out
    assert not _tagged(kit)


# ── GOH.RELCI: the kit pushes HEAD for its CI, then waits for it (ztools v3.12.2, 2026-11) ───────
#
# A wrapper commits "chore: version X" and calls the kit; that commit was never pushed, so GitHub
# had no run for it and step 0 refused "no CI run for HEAD" -- the release needed a hand push, a
# wait, and a re-run. The kit now pushes the branch (the gated push, no --no-verify) and reads the
# runs for HEAD once: red refuses, green tags, pending is advisory over the push gate (GOH.CIWAIT).


def _origin_has_runs_only_for_pushed(kit: dict) -> None:
    (kit["gh_state"] / "origin_git").write_text(str(kit["origin"]))


def _git_lines(kit: dict) -> list[str]:
    return kit["git_log"].read_text().splitlines()


def test_an_unpushed_head_is_pushed_then_its_green_ci_tags(kit):
    _with_ci(kit, GREEN)
    _origin_has_runs_only_for_pushed(kit)
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "no CI run for HEAD" not in out, out
    assert _tagged(kit)
    lines = _git_lines(kit)
    first_push = next(i for i, x in enumerate(lines) if x.startswith("git push origin main"))
    tag = next(i for i, x in enumerate(lines) if x.startswith("git tag -a"))
    assert first_push < tag, lines  # pushed for its CI BEFORE the tag was cut
    assert "--no-verify" not in "\n".join(lines), lines  # the gated push, never around it


def test_no_push_with_an_unpushed_head_refuses_and_says_why(kit):
    _with_ci(kit, GREEN)
    _origin_has_runs_only_for_pushed(kit)
    r = run_release(kit, "--no-push")
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "--no-push" in out and "not on origin" in out, out
    assert not any(x.startswith("git push") for x in _git_lines(kit))
    assert not _tagged(kit)


def test_a_pushed_head_is_not_pushed_again_before_its_ci(kit):
    _with_ci(kit, GREEN)
    subprocess.run(
        ["git", "-C", str(kit["proj"]), "push", "-q", "origin", "main"],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    _origin_has_runs_only_for_pushed(kit)
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    pushes = [x for x in _git_lines(kit) if x.startswith("git push")]
    assert pushes == [f"git push origin main {TAG}"], pushes


def test_actions_disabled_with_an_unpushed_head_tags_without_an_early_push(kit):
    _with_ci(kit, None)
    _origin_has_runs_only_for_pushed(kit)
    (kit["gh_state"] / "actions_enabled").write_text("false\n")
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "GitHub Actions is disabled" in out, out
    pushes = [x for x in _git_lines(kit) if x.startswith("git push")]
    assert pushes == [f"git push origin main {TAG}"], pushes


def test_the_dry_run_plans_the_push_and_the_one_read(kit):
    _with_ci(kit, GREEN)
    r = run_release(kit, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "git push origin main" in out and "read once" in out and "wait" not in out, out
    assert not kit["git_log"].exists() or not any(x.startswith("git push") for x in _git_lines(kit))
