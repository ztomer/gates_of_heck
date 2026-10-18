"""A report that says `landed` is a claim: the audit checks each sha against the remote and its CI.

The published evidence the model-choice rule rests on found 45-76% of agent failures look like
success. A ledger outcome is the agent's own `Status:` line, so a cheap tier that fails silently
would read as a cheap tier that works. `scripts/agent_audit.py` therefore takes every landed
report's shas, finds each in a repo under the estate root, and asks GitHub (gh) whether it is on
the default branch and whether CI concluded success on it -- or on the first pushed run that
contains it. A mismatch is `silent-failure`, and counts as a redo in the verdict arithmetic.

Offline-safe: no network, no gh, or `--offline` makes an outcome `unverified`, never a pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

AUDIT = REPO_ROOT / "scripts" / "agent_audit.py"

FAKE_GH = """\
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_FAKE_LOG"], "a") as f:
    f.write(" ".join(args) + "\\n")
path = next(a for a in args[1:] if not a.startswith("-") and args[args.index(a) - 1] != "--jq")
hit = json.load(open(os.environ["GH_FAKE_MAP"])).get(path)
if hit is None:
    sys.stderr.write("gh: Not Found (HTTP 404)\\n")
    sys.exit(1)
sys.stdout.write(hit.get("out", ""))
sys.stderr.write(hit.get("err", ""))
sys.exit(hit.get("code", 0))
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
        stdin=subprocess.DEVNULL, env=hermetic_env(drop_git=True),
    ).stdout.strip()  # fmt: skip


@pytest.fixture
def estate(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "estate"
    repo = root / "r1"
    repo.mkdir(parents=True)
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    shas = []
    for i in range(2):
        (repo / "f").write_text(str(i))
        _git(repo, "add", "f")
        _git(repo, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    _git(repo, "remote", "add", "origin", "git@github.com:o/r1.git")
    gh = tmp_path / "gh.py"
    gh.write_text(FAKE_GH)
    return {"root": root, "old": shas[0], "new": shas[1], "gh": gh, "tmp": tmp_path}


def _ledger(tmp: Path, report: str, status: str = "landed") -> Path:
    path = tmp / "ledger.jsonl"
    lines = [
        {"event": "spawn", "time": "2026-10-18T10:00:00+00:00", "session_id": "s",
         "tool_use_id": "t1", "model": "sonnet", "effort": "high", "tags": [], "exempt": False,
         "description": "d", "first_line": "item"},
        {"event": "outcome", "session_id": "s", "tool_use_id": "t1", "status": status,
         "report": report, "tokens": {"total": 1}},
    ]  # fmt: skip
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


def _audit(est: dict[str, Any], report: str, gh_map: dict[str, Any], *args: str) -> dict[str, Any]:
    tmp = est["tmp"]
    (tmp / "map.json").write_text(json.dumps(gh_map))
    r = subprocess.run(
        [sys.executable, str(AUDIT), "--ledger", str(_ledger(tmp, report)), "--root",
         str(est["root"]), "--json", *args],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL, check=False,
        env=hermetic_env(GOH_GH=f"{sys.executable} {est['gh']}", GH_FAKE_MAP=str(tmp / "map.json"),
                         GH_FAKE_LOG=str(tmp / "gh.log"), XDG_CACHE_HOME=str(tmp / "cache")),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["verification"]


def _on_main(sha: str) -> dict[str, Any]:
    return {"repos/o/r1": {"out": "main\n"},
            f"repos/o/r1/compare/{sha}...main": {"out": "identical\n"}}  # fmt: skip


def _runs(sha: str, line: str) -> dict[str, Any]:
    return {f"repos/o/r1/actions/runs?head_sha={sha}&per_page=20": {"out": line}}


def test_a_landed_sha_on_main_with_green_ci_is_verified(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    gh = _on_main(sha) | _runs(sha, "7\tCI\tcompleted\tsuccess\n")
    v = _audit(estate, f"Status: landed\n{sha[:9]} fix the leak\nCI run 18234567890 success", gh)
    assert v["verified"] == 1 and v["silent_failure"] == [] and v["unverified"] == []


def test_a_sha_the_remote_does_not_have_is_a_silent_failure(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    gh = {"repos/o/r1": {"out": "main\n"}} | _runs(sha, "7\tCI\tcompleted\tsuccess\n")
    v = _audit(estate, f"Status: landed\n{sha[:9]} fix", gh)  # compare -> HTTP 404
    (row,) = v["silent_failure"]
    assert "not on o/r1" in row["why"] and v["verified"] == 0


def test_a_sha_whose_ci_failed_is_a_silent_failure(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    gh = _on_main(sha) | _runs(sha, "7\tCI\tcompleted\tsuccess\n9\tCI\tcompleted\tfailure\n")
    (row,) = _audit(estate, f"Status: landed\n{sha[:9]} fix", gh)["silent_failure"]
    assert "failure" in row["why"] and "9" in row["why"]


def test_a_claimed_sha_in_no_repo_is_a_silent_failure(estate: dict[str, Any]) -> None:
    (row,) = _audit(estate, "Status: landed\nabc1234def fix", {})["silent_failure"]
    assert "abc1234def" in row["why"]


def test_ci_on_a_later_push_that_contains_the_sha_counts(estate: dict[str, Any]) -> None:
    old, new = estate["old"], estate["new"]
    gh = _on_main(old) | _runs(old, "") | {
        "repos/o/r1/actions/runs?branch=main&per_page=50": {
            "out": f"12\tCI\t{new}\tcompleted\tsuccess\n"}}  # fmt: skip
    v = _audit(estate, f"Status: landed\n{old[:9]} fix", gh)
    assert v["verified"] == 1, v


def test_no_ci_run_yet_is_unverified_not_a_pass(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    v = _audit(estate, f"Status: landed\n{sha[:9]} fix", _on_main(sha) | _runs(sha, ""))
    assert v["verified"] == 0 and len(v["unverified"]) == 1 and v["silent_failure"] == []


def test_an_unreachable_github_is_unverified_not_a_pass(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    down = {"code": 1, "err": "error connecting to api.github.com\n"}
    v = _audit(estate, f"Status: landed\n{sha[:9]} fix", {"repos/o/r1": down})
    assert v["verified"] == 0 and v["silent_failure"] == []
    assert "unreachable" in v["unverified"][0]["why"]


def test_offline_never_calls_gh_and_never_passes(estate: dict[str, Any]) -> None:
    sha = estate["new"]
    gh = _on_main(sha) | _runs(sha, "7\tCI\tcompleted\tsuccess\n")
    v = _audit(estate, f"Status: landed\n{sha[:9]} fix", gh, "--offline")
    assert v["verified"] == 0 and len(v["unverified"]) == 1
    assert not (estate["tmp"] / "gh.log").exists()


def test_a_landed_report_without_shas_is_counted_not_judged(estate: dict[str, Any]) -> None:
    v = _audit(estate, "Status: landed\nnothing to commit; CI run 18234567890 green", {})
    assert v["no_shas"] == 1 and v["verified"] == 0 and v["silent_failure"] == []


def test_a_repo_without_github_ci_is_verified_by_being_on_its_branch(
    estate: dict[str, Any],
) -> None:
    """goh has no workflows: its pre-push gate is its CI, and a sha on main got through it."""
    sha = estate["new"]
    gh = _on_main(sha) | _runs(sha, "") | {"repos/o/r1/actions/workflows": {"out": "0\n"}}
    assert _audit(estate, f"Status: landed\n{sha[:9]} fix", gh)["verified"] == 1


def test_a_github_remote_not_named_origin_is_still_found(estate: dict[str, Any]) -> None:
    repo, sha = estate["root"] / "r1", estate["new"]
    _git(repo, "remote", "rename", "origin", "v2origin")  # ZoneWM's shape
    gh = _on_main(sha) | _runs(sha, "7\tCI\tcompleted\tsuccess\n")
    assert _audit(estate, f"Status: landed\n{sha[:9]} fix", gh)["verified"] == 1
