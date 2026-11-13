"""goh ship reads hosted CI once and never waits on it, and ships with no release under --bump none
(GOH.CIWAIT; split from tests/test_ship.py at the line cap, whose fixtures and fakes these use).

Hosted CI is advisory (owner 2026-11-11; .loop LOOP.CIADVISORY): `loopctl done` closes an item on
the local push gate while its run is queued or running, and `loopctl tick` re-reads it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_ship import FAKE_LOOPCTL, _calls, _result, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


def _on_github(repo: Path) -> None:
    """The remote READS as GitHub's (o/n), so the ship asks for a CI verdict; pushes still go to
    the local bare clone."""
    bare = git(repo, "remote", "get-url", "origin").strip()
    git(repo, "remote", "set-url", "origin", "https://github.com/o/n.git")
    git(repo, "remote", "set-url", "--push", "origin", bare)


def _ci_calls(tmp_path: Path) -> list[list[str]]:
    return [c for c in _calls(tmp_path) if c[0] == "ci"]


def _ci_env(tmp_path: Path, verdicts: str) -> dict[str, str]:
    return {"FAKE_CI": verdicts, "FAKE_CI_N": str(tmp_path / "ci.n")}


def _push_gated(repo: Path) -> None:
    """REPO's pre-push hook is goh's push gate (what `loopctl done` reads as the local record that
    the commit passed the full gate before it could land)."""
    hook = repo / git(repo, "rev-parse", "--git-path", "hooks/pre-push").strip()
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        '#!/bin/sh\n# exec "$GOH_DIR/gates/push_gate.sh" (a stand-in that passes)\nexit 0\n'
    )
    hook.chmod(0o755)


PENDING_CARD = {"FAKE_VERIFY_RC": "1", "FAKE_CARD_ERR": "CI: 1 run still in_progress for abc"}


def test_a_pending_ci_verdict_is_advisory_and_the_ship_never_waits(shipped, tmp_path):
    """GOH.CIWAIT: hosted CI is advisory (owner 2026-11-11; .loop LOOP.CIADVISORY). The ship sat
    in verify polling CI every 60 s (the gates_of_heck v0.109.0 ship: four reads, 3+ minutes) for a
    run that re-runs the gate the push already passed. Now CI is read ONCE: a queued or running run
    on a push-gated checkout passes, the card's CI line with it, and `loopctl done` records the run
    for tick to re-read."""
    _on_github(shipped)
    _push_gated(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE",
              **_ci_env(tmp_path, "pending,green"), **PENDING_CARD)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    ci = _ci_calls(tmp_path)
    head = git(shipped, "rev-parse", "HEAD").strip()
    assert ci == [["ci", head, "--repo", "o/n"]], ci  # one read, never --wait
    log = Path(next(s for s in res["steps"] if s["name"] == "verify")["log"]).read_text()
    assert "advisory" in log and "tick re-reads" in log, log
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] in _calls(tmp_path)


def test_a_pending_ci_verdict_without_the_push_gate_fails_at_once_naming_it(shipped, tmp_path):
    """No push-gate hook: nothing local says the commit was gated, so a pending run is not advisory
    (done refuses it the same way); the ship says so at once instead of waiting."""
    _on_github(shipped)
    r = _ship(shipped, tmp_path, "--wait", **_ci_env(tmp_path, "pending"), **PENDING_CARD)
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "verify", res
    assert any("push gate" in ln for ln in res["tail"]), res["tail"]
    assert len(_ci_calls(tmp_path)) == 1


def test_an_advisory_ci_never_excuses_another_failing_card_line(shipped, tmp_path):
    _on_github(shipped)
    _push_gated(shipped)
    err = "CI: 1 run still queued for abc|tag: newest v1.0.0 peels to abc"
    card = {"FAKE_VERIFY_RC": "1", "FAKE_CARD_ERR": err}
    r = _ship(shipped, tmp_path, "--wait", **_ci_env(tmp_path, "pending"), **card)
    assert r.returncode == 1, r.stdout + r.stderr
    assert _result(tmp_path)["failed_step"] == "verify"


def test_a_red_ci_verdict_fails_the_ship_at_verify(shipped, tmp_path):
    _on_github(shipped)
    _push_gated(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", **_ci_env(tmp_path, "red"))
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "verify", res
    assert any("CI red" in ln for ln in res["tail"]), res["tail"]
    assert not [c for c in _calls(tmp_path) if c[0] == "verify"], "verify ran on a red CI"
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] not in _calls(tmp_path)


def test_no_ship_path_waits_on_hosted_ci():
    """The class, not the instance: neither the ship nor the release kit it runs polls hosted CI
    (GOH.CIWAIT named ship.py's ci_wait and ci_step.sh's ci_watch; a third would be a new sleep)."""
    ship = (REPO_ROOT / "tools/ship.py").read_text() + (
        REPO_ROOT / "lib/ship_verify.py"
    ).read_text()
    kit = (REPO_ROOT / "tools/release-kit/ci_step.sh").read_text()
    for gone in ("time.sleep", "GOH_SHIP_CI_POLL", '"--wait", "--timeout"', "def ci_wait"):
        assert gone not in ship, gone
    for gone in (
        "sleep ",
        "GOH_RELEASE_CI_WAIT",
        "GOH_RELEASE_CI_POLL",
        "ci_watch",
        "gh run watch",
    ):
        assert gone not in kit, gone


def test_no_github_remote_asks_for_no_ci_verdict(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", **_ci_env(tmp_path, "red"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ci_calls(tmp_path) == []


# ── GOH.CIWAIT: --bump none ships with no release (two agents hit its absence, 2026-11-13) ────────


def test_bump_none_on_an_untagged_repo_ships_without_a_release(repo, tmp_path):
    """A repo that declares no version (no v-tag) ships its push, install and card, no release."""
    remote = tmp_path / "remote.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    (repo / "f.txt").write_text("change\n")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fix: change")
    (tmp_path / "loopctl.py").write_text(FAKE_LOOPCTL)
    r = _ship(repo, tmp_path, "--wait", "--bump", "none")
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    steps = {s["name"]: s for s in res["steps"]}
    assert res["version"] == "" and steps["release"]["status"] == "skipped", res
    assert "--bump none" in steps["release"]["why"], steps["release"]
    assert git(repo, "tag", "--list").strip() == ""


def test_bump_none_on_a_tagged_head_ships_under_that_tag(shipped, tmp_path):
    """The change already went out under its version (HEAD carries it): push and record, no bump."""
    git(shipped, "tag", "v1.0.1")
    r = _ship(shipped, tmp_path, "--wait", "--bump", "none")
    assert r.returncode == 0, r.stdout + r.stderr
    steps = {s["name"]: s["status"] for s in _result(tmp_path)["steps"]}
    assert steps["release"] == "skipped", steps
    assert sorted(git(shipped, "tag", "--list").split()) == ["v1.0.0", "v1.0.1"]


def test_bump_none_past_the_newest_tag_is_refused_naming_the_flag(shipped, tmp_path):
    """A tagging repo whose HEAD is past its newest tag cannot ship untagged (the card reads the
    tag): the refusal names --version X.Y.Z for a version a commit already bumped, and the bumps."""
    r = _ship(shipped, tmp_path, "--wait", "--bump", "none")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--version X.Y.Z" in r.stderr and "v1.0.0" in r.stderr, r.stderr
    assert "nothing started" in r.stderr
    assert not (tmp_path / "ship.json").exists()
