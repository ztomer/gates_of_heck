"""scripts/actions_sweep.py: the policy, in both directions, against a fake `gh` (no network).

The policy (G14): artifacts older than 14 days go (an expired one is already gone); a cache goes
when its ref is not the default branch or it was last used more than 7 days ago, and a default-branch
cache in use stays; an archived repo is not touched; artifact-and-log retention is set to 14 days
when it is higher, never raised; a repo that fails is reported without stopping the rest, and the
exit says so; no gh call can hang the job.
"""

import datetime as dt
import json
import os
import subprocess
import sys

import pytest
from _fake_gh_actions import FAKE_GH
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "actions_sweep.py"
NOW = dt.datetime.now(dt.UTC)


def _ago(days: float) -> str:
    return (NOW - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _art(i, age, expired=False, size=1_000_000):
    return {"id": i, "created_at": _ago(age), "expired": expired, "size_in_bytes": size}


def _cache(i, ref, used_days_ago, size=2_000_000):
    return {"id": i, "ref": ref, "last_accessed_at": _ago(used_days_ago), "size_in_bytes": size}


def _repo(name, archived=False, branch="main"):
    return {
        "nameWithOwner": f"o/{name}",
        "isArchived": archived,
        "defaultBranchRef": {"name": branch} if branch else None,
    }


class Sweep:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        bindir = tmp_path / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.state, self.log = tmp_path / "state.json", tmp_path / "log"
        self.log.write_text("")
        self.env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state),
            "FAKE_GH_LOG": str(self.log),
            "NO_COLOR": "1",
        }

    def run(self, repos, data, *args, timeout=60):
        self.state.write_text(
            json.dumps({"repos": repos, "data": {f"o/{k}": v for k, v in data.items()}})
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--owner", "o", *args],
            env=self.env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )

    def calls(self, verb):
        return [ln for ln in self.log.read_text().splitlines() if f"-X {verb}" in ln]


@pytest.fixture
def sweep(tmp_path):
    return Sweep(tmp_path)


def test_dry_run_changes_nothing_but_reports_the_policy(sweep):
    data = {
        "r": {
            "artifacts": [_art(1, 30)],
            "caches": [_cache(7, "refs/heads/old", 1)],
            "retention": 90,
        }
    }
    p = sweep.run([_repo("r")], data)
    assert p.returncode == 0, p.stderr
    assert "would delete 1 artifacts" in p.stdout and "1 caches" in p.stdout
    assert sweep.calls("DELETE") == [] and sweep.calls("PUT") == []


def test_artifact_policy_old_goes_new_stays_expired_is_not_counted(sweep):
    data = {
        "r": {"artifacts": [_art(1, 20), _art(2, 2), _art(3, 40, expired=True)], "retention": 14}
    }
    p = sweep.run([_repo("r")], data, "--apply")
    assert p.returncode == 0, p.stderr
    assert sweep.calls("DELETE") == ["api -X DELETE repos/o/r/actions/artifacts/1"]


def test_cache_policy_branch_goes_default_stale_goes_default_fresh_stays(sweep):
    caches = [
        _cache(1, "refs/heads/feature", 0.1),  # off the default branch: goes even though fresh
        _cache(2, "refs/pull/9/merge", 0.1),  # a PR merge ref: goes
        _cache(3, "refs/heads/main", 10),  # default branch, stale: goes
        _cache(4, "refs/heads/main", 1),  # default branch, in use: stays
    ]
    p = sweep.run([_repo("r")], {"r": {"caches": caches, "retention": 14}}, "--apply")
    assert p.returncode == 0, p.stderr
    gone = sorted(ln.rsplit("/", 1)[1] for ln in sweep.calls("DELETE"))
    assert gone == ["1", "2", "3"]


def test_default_branch_is_the_repos_own_not_always_main(sweep):
    caches = [_cache(1, "refs/heads/trunk", 1), _cache(2, "refs/heads/main", 1)]
    p = sweep.run(
        [_repo("r", branch="trunk")], {"r": {"caches": caches, "retention": 14}}, "--apply"
    )
    assert p.returncode == 0, p.stderr
    assert [ln.rsplit("/", 1)[1] for ln in sweep.calls("DELETE")] == ["2"]


def test_archived_repo_is_not_touched(sweep):
    data = {"old": {"artifacts": [_art(1, 90)], "retention": 90}, "live": {"retention": 14}}
    p = sweep.run([_repo("old", archived=True), _repo("live")], data, "--apply")
    assert p.returncode == 0, p.stderr
    assert "repos/o/old" not in sweep.log.read_text()


def test_pagination_reads_every_concatenated_page(sweep):
    # five artifacts = three pages of two, concatenated as `gh api --paginate` prints them
    data = {"r": {"artifacts": [_art(i, 30) for i in range(1, 6)], "retention": 14}}
    p = sweep.run([_repo("r")], data, "--apply")
    assert p.returncode == 0, p.stderr
    assert len(sweep.calls("DELETE")) == 5


def test_retention_is_lowered_when_higher_and_never_raised(sweep):
    data = {"high": {"retention": 90}, "equal": {"retention": 14}, "low": {"retention": 5}}
    p = sweep.run([_repo("high"), _repo("equal"), _repo("low")], data, "--apply")
    assert p.returncode == 0, p.stderr
    assert sweep.calls("PUT") == [
        "api -X PUT repos/o/high/actions/permissions/artifact-and-log-retention -F days=14"
    ]
    assert "retention on 1" in p.stdout


def test_dry_run_does_not_put_retention(sweep):
    p = sweep.run([_repo("high")], {"high": {"retention": 90}})
    assert p.returncode == 0, p.stderr
    assert "would set retention on 1" in p.stdout and sweep.calls("PUT") == []


def test_a_failing_repo_is_reported_and_does_not_stop_the_rest(sweep):
    data = {"bad": {"fail": True}, "good": {"artifacts": [_art(1, 30)], "retention": 14}}
    p = sweep.run([_repo("bad"), _repo("good")], data, "--apply")
    assert p.returncode == 1
    assert "o/bad" in p.stderr and "boom" in p.stderr and "1 failed" in p.stderr
    assert sweep.calls("DELETE") == ["api -X DELETE repos/o/good/actions/artifacts/1"]


def test_a_hung_gh_call_is_bounded_and_counts_as_that_repos_failure(sweep):
    data = {"stuck": {"hang": True}, "good": {"artifacts": [_art(1, 30)], "retention": 14}}
    p = sweep.run([_repo("stuck"), _repo("good")], data, "--apply", "--timeout", "1", timeout=30)
    assert p.returncode == 1
    assert "timed out" in p.stderr
    assert len(sweep.calls("DELETE")) == 1


def test_nothing_to_do_is_one_line(sweep):
    p = sweep.run([_repo("r")], {"r": {"retention": 14}}, "--apply")
    assert p.returncode == 0
    assert p.stdout.strip().count("\n") == 0 and "nothing to do" in p.stdout


def test_cannot_list_repos_is_a_failure(sweep):
    sweep.state.write_text("{}")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--owner", "o"],
        env=sweep.env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    assert p.returncode == 1 and "cannot list" in p.stderr
