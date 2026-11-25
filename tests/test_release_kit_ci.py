"""The release kit refuses to tag over a red CI run on HEAD (BACKLOG 6.5), and reads CI once, never
waiting: a pending or missing run is ADVISORY over goh's push gate (GOH.CIWAIT, owner 2026-11-11).

ztools' CI has been red since 2026-10-10 (`.python-version` is gitignored and ci.yml reads it),
and v3.6.0, v3.7.0 and v3.8.0 were each tagged over it. The kit's first step now reads the runs
for HEAD (`gh run list --commit`); the fake gh lists what a test plants in runs.tsv. A repo with
no workflows says "no CI", which is not a green.

The step's decisions are asked of the step itself, sourced as release.sh sources it (`_step`,
GOH.PYSLOW2: a whole release per decision was sixteen); that release.sh stops on its refusal, tags
on its pass, and pushes before it, is the three releases at the end.
"""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, hermetic_env
from test_release_kit import (
    RELEASE,
    TAG,
    commit_all,
    kit,  # noqa: F401  (a fixture)
    run_release,
)

sys.path.insert(0, str(REPO_ROOT / "lib"))
import push_gated as gated

sys.path.pop(0)

RED = "completed\tfailure\tci\thttps://example.invalid/runs/1\n"
PENDING = "in_progress\t\tci\thttps://example.invalid/runs/2\n"
GREEN = "completed\tsuccess\tci\thttps://example.invalid/runs/3\ncompleted\tskipped\tdocs\thttps://example.invalid/runs/4\n"


def _with_ci(kit: dict) -> None:
    wf = kit["proj"] / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\njobs: {}\n")
    commit_all(kit["proj"], "ci")


def _tagged(kit: dict) -> bool:
    return (kit["proj"] / ".git" / "refs" / "tags" / TAG).exists()


def _push_gated(kit: dict) -> None:
    """The checkout's pre-push hook is goh's push gate -- the stock shim, judged by its bytes
    (lib/push_gated.py, GOH.SHIMPUSHGATED) -- the local record that a commit passed the full gate
    before it could be pushed (what .loop's `loopctl done` reads the same way). HEAD is on origin,
    as a landed commit is, and the release runs --no-push: the shim runs the REAL push gate, which
    has no gate of record in this fixture."""
    hook = kit["proj"] / ".git" / "hooks" / "pre-push"
    hook.write_bytes(gated.shim())
    hook.chmod(0o755)
    subprocess.run(["git", "-C", str(kit["proj"]), "push", "-q", "--no-verify", "origin", "HEAD"],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)  # fmt: skip


def _run_lists(kit: dict) -> int:
    log = kit["gh_log"]
    return sum("run list" in ln for ln in log.read_text().splitlines()) if log.exists() else 0


def _git_lines(kit: dict) -> list[str]:
    return kit["git_log"].read_text().splitlines()


def _pushes(kit: dict) -> list[str]:
    return [x for x in _git_lines(kit) if x.startswith("git push")]


# The step as release.sh runs it: its own helpers (the three one-line functions it defines), its own
# sourced files, the globals its argument parser and its branch step leave.
STEP = """
set -euo pipefail
GOH="{goh}"; . "$GOH/tui/lib.sh"
{helpers}
. "$GOH/tools/release-kit/ci_step.sh"
release_ci_step "$CI_CHECK"
"""


def _helpers() -> str:
    lines = RELEASE.read_text().splitlines()
    got = [ln for ln in lines if ln.startswith(("plan() {", "begin() {", "fail() {"))]
    assert len(got) == 3, got
    return "\n".join(got)


def _step(kit: dict, runs: str | None, **globals_: str) -> tuple[bool, str, int]:
    """(passed, what it said, how often it read CI) for RUNS planted as HEAD's."""
    tsv = kit["gh_state"] / "runs.tsv"
    tsv.unlink(missing_ok=True) if runs is None else tsv.write_text(runs)
    for log in ("gh_log", "git_log"):
        kit[log].write_text("")
    env = hermetic_env(GH_STATE=str(kit["gh_state"]), GH_LOG=str(kit["gh_log"]),
                       GOHKIT_GIT_LOG=str(kit["git_log"]))  # fmt: skip
    env["PATH"] = f"{kit['bin']}:{env['PATH']}"
    env.update({"CI_CHECK": "1", "DRY_RUN": "0", "DO_PUSH": "1", "BRANCH": "main",
                "PUSH_REFSPEC": "main", "GH_REPO_SLUG": "t/proj", "STEP": "",
                "ORIGIN_URL": str(kit["origin"]), **globals_})  # fmt: skip
    r = subprocess.run(["bash", "-c", STEP.format(goh=REPO_ROOT, helpers=_helpers())],
                       cwd=kit["proj"], env=env, capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    return r.returncode == 0, r.stdout + r.stderr, _run_lists(kit)


def test_the_step_without_a_push_gate_refuses_whatever_is_not_green(kit):
    """No local record says HEAD was gated: red, pending and no run at all each refuse after one
    read; green passes; a repo with no workflows, and --no-ci-check, are said and read nothing."""
    passed, out, reads = _step(kit, None)
    assert passed and "no CI:" in out and "not a green" in out and reads == 0, out
    _with_ci(kit)
    passed, out, reads = _step(kit, RED)
    assert not passed and "'ci'" in out and "ci: failure" in out and "runs/1" in out, out
    passed, out, reads = _step(kit, PENDING)
    assert not passed and "ci: in_progress" in out and "push gate" in out, out
    assert reads == 1, "the kit read CI more than once"
    passed, out, _ = _step(kit, None)
    assert not passed and "no CI run for HEAD" in out, out
    passed, out, _ = _step(kit, GREEN)
    assert passed and "2 CI runs" in out, out
    passed, out, reads = _step(kit, RED, CI_CHECK="0")
    assert passed and "skipped (--no-ci-check)" in out and reads == 0, out


def test_over_the_push_gate_a_pending_or_missing_run_is_advisory_and_a_red_one_refuses(kit):
    """GOH.CIWAIT: the kit watched CI up to 1800 s for a run re-running the gate the push already
    passed. Over goh's push gate a pending run is advisory: passed now, said, read once."""
    _with_ci(kit)
    _push_gated(kit)
    passed, out, reads = _step(kit, PENDING)
    assert passed and "advisory" in out and "ci: in_progress" in out, out
    assert reads == 1, "the kit read CI more than once"
    passed, out, _ = _step(kit, None)
    assert passed and "advisory" in out, out
    passed, out, _ = _step(kit, RED)
    assert not passed and "ci: failure" in out, out
    assert _pushes(kit) == [], "a HEAD origin has was pushed"


# ── R3.13: Actions DISABLED is said, not waited on (sys_updater, 2026-10) ─────────────────────────
#
# sys_updater tracks workflows and has GitHub Actions disabled: no run ever comes, so the kit
# refused "no CI run for HEAD" and the release went out with --no-ci-check -- a skip that also
# skips every repo where the run is genuinely missing. The kit asks the API instead
# (`repos/OWNER/NAME/actions/permissions`), and a disabled repo is "no CI", which is not a green.


def test_actions_disabled_is_named_and_passes_with_no_early_push(kit):
    _with_ci(kit)
    _origin_has_runs_only_for_pushed(kit)
    (kit["gh_state"] / "actions_enabled").write_text("false\n")
    passed, out, reads = _step(kit, None)  # HEAD is not on origin
    assert passed and "GitHub Actions is disabled" in out and "not a green" in out, out
    assert "no CI run for HEAD" not in out and reads == 0, out
    assert _pushes(kit) == [], "pushed for a CI that will never come"
    (kit["gh_state"] / "actions_enabled").write_text("true\n")
    passed, out, _ = _step(kit, None)
    assert not passed and "no CI run for HEAD" in out, out


# ── GOH.RELCI: the kit pushes HEAD for its CI, then waits for it (ztools v3.12.2, 2026-11) ───────
#
# A wrapper commits "chore: version X" and calls the kit; that commit was never pushed, so GitHub
# had no run for it and step 0 refused "no CI run for HEAD" -- the release needed a hand push, a
# wait, and a re-run. The kit now pushes the branch (the gated push, no --no-verify) and reads the
# runs for HEAD once: red refuses, green tags, pending is advisory over the push gate (GOH.CIWAIT).


def _origin_has_runs_only_for_pushed(kit: dict) -> None:
    (kit["gh_state"] / "origin_git").write_text(str(kit["origin"]))


def test_an_unpushed_head_is_pushed_once_for_its_ci_and_never_under_no_push(kit):
    _with_ci(kit)
    _origin_has_runs_only_for_pushed(kit)
    passed, out, _ = _step(kit, GREEN, DO_PUSH="0")
    assert not passed and "--no-push" in out and "not on origin" in out, out
    assert _pushes(kit) == []
    passed, out, _ = _step(kit, GREEN, DRY_RUN="1")
    assert passed, out
    assert "git push origin main" in out and "read once" in out and "wait" not in out, out
    assert _pushes(kit) == []
    passed, out, _ = _step(kit, GREEN)
    assert passed and "2 CI runs" in out, out
    assert _pushes(kit) == ["git push origin main"], _pushes(kit)
    passed, out, _ = _step(kit, GREEN)  # origin has it now
    assert passed and _pushes(kit) == [], _pushes(kit)


# ── through release.sh ────────────────────────────────────────────────────────────────────────────


def test_a_release_stops_on_the_steps_refusal_and_no_ci_check_skips_it(kit):
    _with_ci(kit)
    (kit["gh_state"] / "runs.tsv").write_text(RED)
    r = run_release(kit)
    out = r.stdout + r.stderr
    assert r.returncode != 0 and "ci: failure" in out and "runs/1" in out, out
    assert not _tagged(kit)
    r = run_release(kit, "--no-ci-check", "--no-push")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "skipped (--no-ci-check)" in out, out
    assert _tagged(kit)


def test_a_release_pushes_an_unpushed_head_then_tags_on_its_green_ci(kit):
    _with_ci(kit)
    (kit["gh_state"] / "runs.tsv").write_text(GREEN)
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
    assert _pushes(kit)[1:] == [f"git push origin main {TAG}"], _pushes(kit)


def test_a_release_over_the_push_gate_tags_on_a_pending_run(kit):
    _with_ci(kit)
    _push_gated(kit)
    (kit["gh_state"] / "runs.tsv").write_text(PENDING)
    r = run_release(kit, "--no-push")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "advisory" in out and "ci: in_progress" in out, out
    assert _tagged(kit)
