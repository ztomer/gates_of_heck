"""tools/ship.py (`goh ship`): the detached, time-bounded local landing pipeline (GOH.SHIP).

An agent's work ends at "committed, local gate green, goh ship started"; what follows is
push (through the pre-push gate) -> the repo's release -> install -> the `loopctl verify` card ->
`loopctl done`, run by nobody's turn. So the three things that must hold without anyone watching:
the success path records the item done, a failing step names itself and carries its log tail (the
coordinator resumes an agent with only that), and the time bound kills a hung step's whole process
group, leaving no orphan. The loopctl here is a fake that records its argv; the real one is
exercised end to end by the coordinator's repo (.loop tests/test_ship_done.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env, run_goh
from timing_bounds import GiveUp, assert_sooner

SHIP = REPO_ROOT / "tools" / "ship.py"
# The fake loopctl: every call appended to $FAKE_LOOPCTL_LOG; `verify` exits $FAKE_VERIFY_RC.
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "verify":
    print("fake card line 1")
    sys.exit(int(os.environ.get("FAKE_VERIFY_RC", "0")))
if sys.argv[1] == "ci":  # $FAKE_CI: the verdicts in order (the last repeats), counted in $FAKE_CI_N
    states = os.environ["FAKE_CI"].split(",")
    n_file = os.environ["FAKE_CI_N"]
    n = int(open(n_file).read()) if os.path.exists(n_file) else 0
    open(n_file, "w").write(str(n + 1))
    state = states[min(n, len(states) - 1)]
    print(f"CI {state}: fake verdict {n + 1}")
    sys.exit({"green": 0, "red": 1, "pending": 3, "none": 4, "throttled": 75}[state])
"""


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


def make_shipped(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a bare `origin` it tracks, tagged v1.0.0 and pushed, then one commit on top."""
    remote = tmp_path / "remote.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "tag", "v1.0.0")
    git(repo, "push", "-q", "-u", "origin", "main", "v1.0.0")
    (repo / "f.txt").write_text("change\n")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: change")
    (tmp_path / "loopctl.py").write_text(FAKE_LOOPCTL)
    return repo


def _env(tmp_path: Path, **over: str) -> dict[str, str]:
    base = {
        "GOH_LOOPCTL": str(tmp_path / "loopctl.py"),
        "FAKE_LOOPCTL_LOG": str(tmp_path / "loopctl.calls"),
        "LOOPCTL_ROOT": str(tmp_path),
        "GOH_SHIP_RELEASE": 'git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"',
    }
    return hermetic_env(drop_git=True, **{**base, **over})


def _ship(repo: Path, tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    if (repo / "CHANGELOG.md").is_file():  # goh's changelog_cut contract unless a test names one
        env.setdefault("GOH_SHIP_CHANGELOG", "cut")
    return subprocess.run([sys.executable, str(SHIP), "--repo", str(repo), "--out",
                           str(tmp_path / "ship.json"), *args],
                          env=_env(tmp_path, **env), capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _result(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ship.json").read_text())


def _calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "loopctl.calls"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_the_success_path_ships_every_step_and_records_the_item_done(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", "--summary", "did the thing")
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    assert [(s["name"], s["status"]) for s in res["steps"]] == [
        ("push", "ok"),
        ("release", "ok"),
        ("install", "skipped"),
        ("verify", "ok"),
        ("done", "ok"),
    ]
    assert res["version"] == "1.1.0"  # feat: since v1.0.0 -> minor
    assert git(shipped, "ls-remote", "origin", "refs/tags/v1.1.0").strip()
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] in _calls(tmp_path)
    # nothing but a mark per step is printed
    marks = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert all(("✓" in ln) or ("·" in ln) for ln in marks), r.stdout
    assert len(marks) == 5


@pytest.mark.parametrize("step", ["push", "release", "install", "verify"])
def test_a_failing_step_names_itself_and_carries_its_last_40_log_lines(shipped, tmp_path, step):
    noisy = 'for i in $(seq 1 100); do echo "line $i"; done; exit 3'
    env: dict[str, str] = {}
    if step == "push":
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text("#!/bin/sh\n" + noisy + "\n")
        (hooks / "pre-push").chmod(0o755)
        git(shipped, "config", "core.hooksPath", str(hooks))
    elif step == "release":
        env["GOH_SHIP_RELEASE"] = noisy
    elif step == "install":
        env["GOH_SHIP_INSTALL"] = noisy
    else:
        env["FAKE_VERIFY_RC"] = "1"
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", **env)
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "failed"
    assert res["failed_step"] == step
    if step == "verify":
        assert "fake card line 1" in res["tail"]
    elif step == "push":  # git's own refusal follows the hook's output
        assert "line 100" in res["tail"] and len(res["tail"]) == 40
    else:
        assert res["tail"][-1] == "line 100" and res["tail"][0] == "line 61"
        assert len(res["tail"]) == 40
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] not in _calls(tmp_path)
    assert f"✗ {step}" in r.stdout + r.stderr


def _sleepers(marker: str) -> list[str]:
    ps = subprocess.run(["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True,
                        check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return [ln for ln in ps.splitlines() if marker in ln and "ps -Ao" not in ln]


def test_the_bound_kills_a_hung_step_and_its_whole_group_leaving_no_orphan(shipped, tmp_path):
    marker = f"sleep 731.{os.getpid() % 1000}"
    hung = f"{marker} & {marker} & ( {marker} & wait ) & wait"
    t0 = time.monotonic()
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=hung, GOH_SHIP_TIMEOUT="2")
    # the pass: the 2 s bound, then up to the 5 s grace; the failure: the sleeps run out (731 s)
    assert_sooner(time.monotonic() - t0, 8, 731, "a hung step under a 2 s bound")
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "release"
    assert res["steps"][-1]["status"] == "timed_out"
    left = _sleepers(marker)
    assert not left, f"orphans (PPID=1 once their shell died): {left}"


def test_started_detached_it_returns_at_once_and_the_worker_finishes_alone(shipped, tmp_path):
    t0 = time.monotonic()
    r = _ship(shipped, tmp_path, "--item", "X.ONE", GOH_SHIP_INSTALL="sleep 8")
    assert r.returncode == 0, r.stdout + r.stderr
    # the pass: plan and spawn; the failure: a start that ran the pipeline, its 8 s install included
    assert_sooner(time.monotonic() - t0, 1.5, 8, "goh ship's detached start")
    assert "goh ship started" in r.stdout
    assert str(tmp_path / "ship.json") in r.stdout
    give_up = GiveUp(90)
    while not give_up.passed() and _result(tmp_path)["status"] == "running":
        time.sleep(0.5)
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    status = subprocess.run([sys.executable, str(SHIP), "--status", str(tmp_path / "ship.json")],
                            capture_output=True, text=True, check=False,
                            stdin=subprocess.DEVNULL, env=_env(tmp_path))  # fmt: skip
    assert status.returncode == 0, status.stdout + status.stderr


def test_a_repo_that_tags_but_declares_no_release_is_refused_before_it_starts(shipped, tmp_path):
    r = _ship(shipped, tmp_path, GOH_SHIP_RELEASE="")
    assert r.returncode == 2
    assert "GOH_SHIP_RELEASE" in r.stderr
    assert not (tmp_path / "ship.json").exists()


def test_a_dirty_tree_is_refused_before_it_starts(shipped, tmp_path):
    (shipped / "README.md").write_text("dirty\n")
    r = _ship(shipped, tmp_path)
    assert r.returncode == 2
    assert "uncommitted" in r.stderr


def test_a_head_already_tagged_skips_the_release(shipped, tmp_path):
    git(shipped, "tag", "v1.0.1")
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 0, r.stdout + r.stderr
    rel = next(s for s in _result(tmp_path)["steps"] if s["name"] == "release")
    assert rel["status"] == "skipped" and "v1.0.1" in rel["why"]


def _refusing_pre_push(repo: Path, tmp_path: Path) -> None:
    """A pre-push hook that refuses every push it gates: the hand-off alone is not pushable."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\necho 'note ahead of the version'; exit 1\n")
    (hooks / "pre-push").chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))


# A release kit that commits its bump and pushes the hand-off with it (ZoneWM's tools/release.sh:
# its note may only land in the release commit, so the hand-off alone is refused by its gate).
PUSHING_KIT = ('git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "release" && '
               'git tag "v$GOH_SHIP_VERSION" && '
               'git push -q --no-verify origin HEAD:refs/heads/main "v$GOH_SHIP_VERSION"')  # fmt: skip


def test_a_release_kit_that_pushes_lands_the_hand_off_without_the_separate_push(shipped, tmp_path):
    _refusing_pre_push(shipped, tmp_path)
    r = _ship(
        shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=PUSHING_KIT, GOH_SHIP_RELEASE_PUSHES="1"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    push = next(s for s in res["steps"] if s["name"] == "push")
    assert push["status"] == "skipped" and "release kit pushes" in push["why"], push
    assert git(shipped, "ls-remote", "origin", "refs/heads/main").split()[0] == res["sha"]


def test_without_the_key_the_hand_off_is_pushed_first_and_its_gate_is_heard(shipped, tmp_path):
    _refusing_pre_push(shipped, tmp_path)
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=PUSHING_KIT)
    assert r.returncode == 1
    assert _result(tmp_path)["failed_step"] == "push"


def test_a_pushing_kit_on_a_head_already_tagged_still_pushes(shipped, tmp_path):
    git(shipped, "tag", "v1.0.1")
    r = _ship(
        shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=PUSHING_KIT, GOH_SHIP_RELEASE_PUSHES="1"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    steps = {s["name"]: s["status"] for s in _result(tmp_path)["steps"]}
    assert steps["push"] == "ok" and steps["release"] == "skipped", steps


@pytest.mark.parametrize(
    ("subject", "want"),
    [
        ("fix: a", "1.0.1"),
        ("feat: a", "1.1.0"),
        ("feat!: a", "2.0.0"),
        ("chore: a", "1.0.1"),
        ("not conventional", "1.0.1"),
    ],
)
def test_the_next_version_follows_conventional_commits(shipped, tmp_path, subject, want):
    git(shipped, "reset", "-q", "--hard", "v1.0.0")
    (shipped / "g.txt").write_text(subject)
    git(shipped, "add", "g.txt")
    git(shipped, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", subject)
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["version"] == want


def test_goh_ship_runs_this_file_and_passes_its_exit_code(shipped, tmp_path):
    r = run_goh(shipped, "ship", "--out", str(tmp_path / "ship.json"), "--wait",
                env=_env(tmp_path))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["status"] == "ok"
    r = run_goh(shipped, "ship", "--status", str(tmp_path / "nope.json"), env=_env(tmp_path))
    assert r.returncode == 2 and "nope.json" in r.stderr


def _on_github(repo: Path) -> None:
    """The remote READS as GitHub's (o/n), so the ship asks for a CI verdict; pushes still go to
    the local bare clone."""
    bare = git(repo, "remote", "get-url", "origin").strip()
    git(repo, "remote", "set-url", "origin", "https://github.com/o/n.git")
    git(repo, "remote", "set-url", "--push", "origin", bare)


def _ci_calls(tmp_path: Path) -> list[list[str]]:
    return [c for c in _calls(tmp_path) if c[0] == "ci"]


def _ci_env(tmp_path: Path, verdicts: str) -> dict[str, str]:
    return {"FAKE_CI": verdicts, "FAKE_CI_N": str(tmp_path / "ci.n"), "GOH_SHIP_CI_POLL": "0"}


def test_a_queued_ci_verdict_is_pending_and_the_ship_waits_for_green(shipped, tmp_path):
    """GOH.SHIPCIWAIT: verify ran 1.6 s after the push, read loopci's one queued run as red and
    failed a ship whose CI went green minutes later. Queued is pending: the ship waits on it."""
    _on_github(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE",
              **_ci_env(tmp_path, "pending,pending,green"))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    ci = _ci_calls(tmp_path)
    assert len(ci) == 3, ci
    head = git(shipped, "rev-parse", "HEAD").strip()
    assert ci[0][:4] == ["ci", head, "--repo", "o/n"] and "--wait" in ci[0], ci[0]
    calls = _calls(tmp_path)
    assert calls.index(ci[-1]) < calls.index(["verify", res["name"]]), "verify before green"


def test_a_red_ci_verdict_fails_the_ship_at_verify(shipped, tmp_path):
    _on_github(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", **_ci_env(tmp_path, "red"))
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "verify", res
    assert any("CI red" in ln for ln in res["tail"]), res["tail"]
    assert not [c for c in _calls(tmp_path) if c[0] == "verify"], "verify ran on a red CI"
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] not in _calls(tmp_path)


def test_a_verdict_still_pending_at_the_bound_fails_at_verify_naming_it(shipped, tmp_path):
    _on_github(shipped)
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_TIMEOUT="3", **_ci_env(tmp_path, "pending"))
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "verify", res
    assert any("still pending" in ln for ln in res["tail"]), res["tail"]


def test_no_github_remote_asks_for_no_ci_verdict(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", **_ci_env(tmp_path, "red"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ci_calls(tmp_path) == []


def test_goh_ship_help_is_ship_py_usage_not_clap_help(shipped, tmp_path):
    """GOH.SHIPADOPT: `goh ship --help` printed clap's one-line pass-through help, so the flags the
    pipeline takes were nowhere an agent looked. The help is the script's own."""
    for flag in ("--help", "-h"):
        r = run_goh(shipped, "ship", flag, env=_env(tmp_path))
        assert r.returncode == 0, r.stdout + r.stderr
        assert "usage: goh ship" in r.stdout and "--summary" in r.stdout, r.stdout
    r = run_goh(shipped, "agent-stalls", "--help", env=_env(tmp_path))
    assert "usage:" in r.stdout and "Usage: goh agent-stalls" not in r.stdout, r.stdout


BUMP_RELEASE = ('printf "%s\\n" "$GOH_SHIP_VERSION" > VERSION && git add VERSION && '
                'git -c user.name=t -c user.email=t@t commit -q -m "release: v$GOH_SHIP_VERSION" && '
                'git tag "v$GOH_SHIP_VERSION" && git push -q origin HEAD:refs/heads/main '
                '"v$GOH_SHIP_VERSION"')  # fmt: skip


def test_the_result_names_the_landed_commit_not_the_hand_off(shipped, tmp_path):
    """GOH.SHIPADOPT: a release kit that commits its bump lands a commit the hand-off never had;
    the pilot's result (and so `loopctl done`'s record) named the hand-off commit instead."""
    handoff = git(shipped, "rev-parse", "HEAD").strip()
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=BUMP_RELEASE)
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    landed = git(shipped, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert landed != handoff
    assert res["sha"] == landed, res
    assert res["handoff_sha"] == handoff, res


def _path_with(tmp_path: Path, cliff: str | None) -> str:
    """PATH with git-cliff replaced by CLIFF's script (None: no git-cliff at all)."""
    bin_ = tmp_path / "cliffbin"
    bin_.mkdir(exist_ok=True)
    for tool in ("git", "bash", "python3"):
        found = shutil.which(tool)
        assert found, tool
        if not (bin_ / tool).exists():
            (bin_ / tool).symlink_to(found)
    if cliff is not None:
        (bin_ / "git-cliff").write_text(cliff)
        (bin_ / "git-cliff").chmod(0o755)
    return f"{bin_}:/usr/bin:/bin"


def test_the_next_version_is_git_cliffs_answer(shipped, tmp_path):
    """The bump is the maintained tool's (git-cliff --bumped-version), not a hand copy of its rule:
    whatever it answers is the version, so a repo's own cliff.toml [bump] is honoured."""
    path = _path_with(tmp_path, "#!/bin/sh\necho v7.8.9\n")
    r = _ship(shipped, tmp_path, "--wait", PATH=path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["version"] == "7.8.9"


def test_without_git_cliff_the_ship_is_refused_naming_it(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", PATH=_path_with(tmp_path, None))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "git-cliff" in r.stderr and "brew install git-cliff" in r.stderr, r.stderr
    r = _ship(shipped, tmp_path, "--wait", "--bump", "patch", PATH=_path_with(tmp_path, None))
    assert r.returncode == 0, r.stdout + r.stderr  # an explicit bump needs no tool


def test_a_step_runs_with_none_of_gits_repository_variables(monkeypatch):
    """GOH.ENVLEAK: the steps' scrub is git's OWN list (`--local-env-vars`), never a hand copy. The
    copy held 6 of git's 15: GIT_CONFIG_PARAMETERS, GIT_ALTERNATE_OBJECT_DIRECTORIES and the rest
    reached the release kit and every test its push gate ran."""
    sys.path.insert(0, str(REPO_ROOT / "checks"))
    from _gitutil import local_env_vars

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import ship

    names = local_env_vars()
    assert "GIT_CONFIG_PARAMETERS" in names  # calibration: the list is git's, not the copy's
    for name in names:
        monkeypatch.setenv(name, "/leaked")
    assert not set(names) & set(ship.unbound_env())


def _changelog(repo: Path, text: str) -> None:
    (repo / "CHANGELOG.md").write_text(text)
    git(repo, "add", "CHANGELOG.md")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "docs: changelog")


EMPTY_UNRELEASED = "# CHANGELOG\n\n## Unreleased\n\n## v1.0.0\n\n* the first\n"


@pytest.mark.parametrize("args", [(), ("--item", "X.ONE"), ("--bump", "patch")])
def test_an_empty_unreleased_is_refused_before_the_push(shipped, tmp_path, args):
    """GOH.SHIPUNRELEASED: routines' ship pushed main, then its release kit's changelog_cut said
    `Unreleased is empty: nothing to release`. A release step that will run (with or without
    --bump: the version is git-cliff's otherwise) over an empty Unreleased is refused at start,
    naming the missing entry, and nothing is pushed."""
    _changelog(shipped, EMPTY_UNRELEASED)
    before = git(shipped, "ls-remote", "origin", "refs/heads/main")
    r = _ship(shipped, tmp_path, "--wait", *args)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "Unreleased" in r.stderr and "CHANGELOG.md" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin", "refs/heads/main") == before
    assert not (tmp_path / "ship.json").exists()


def test_an_empty_unreleased_is_not_refused_when_no_release_runs(shipped, tmp_path):
    """HEAD already tagged: the release step is skipped, so nothing is cut and nothing refused."""
    _changelog(shipped, EMPTY_UNRELEASED)
    git(shipped, "tag", "v1.0.1")
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 0, r.stdout + r.stderr


# ---- GOH.SHIPPREFLIGHT: a release precondition knowable before the push is asked before it ----
# token-pool's kit refused after the push ("CHANGELOG.md has no '## Unreleased' section"), a
# private consumer's after its push too ("its plan's done ledger has no row for v5.18.0"). A repo
# declares GOH_SHIP_PREFLIGHT, a command given {version}; plan() runs it (and goh's own changelog
# reading, the same path) before the first step, and a refusal starts nothing.

LEDGER_REFUSES = 'echo "the done ledger has no row for v{version}" >&2; exit 1'


@pytest.mark.parametrize("args", [(), ("--item", "X.ONE"), ("--bump", "patch")])
def test_a_failing_preflight_refuses_with_its_reason_and_leaves_origin_untouched(
    shipped, tmp_path, args
):
    before = git(shipped, "ls-remote", "origin")
    r = _ship(shipped, tmp_path, "--wait", *args, GOH_SHIP_PREFLIGHT=LEDGER_REFUSES)
    assert r.returncode == 2, r.stdout + r.stderr
    version = "1.0.1" if args == ("--bump", "patch") else "1.1.0"
    assert f"the done ledger has no row for v{version}" in r.stderr, r.stderr
    assert "GOH_SHIP_PREFLIGHT" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin") == before
    assert not (tmp_path / "ship.json").exists()
    assert _calls(tmp_path) == []


def test_the_preflight_runs_in_the_repo_with_the_version_and_a_pass_ships(shipped, tmp_path):
    check = ('test -f f.txt && test "$GOH_SHIP_VERSION" = 1.1.0 && test {version} = 1.1.0 '
             '&& echo "$GOH_SHIP_SUMMARY" > ' + str(tmp_path / "seen"))  # fmt: skip
    r = _ship(shipped, tmp_path, "--wait", "--summary", "the title", GOH_SHIP_PREFLIGHT=check)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "seen").read_text().strip() == "the title"
    assert _result(tmp_path)["preflight"] == [check.replace("{version}", "1.1.0")]


def test_no_release_step_runs_no_preflight(shipped, tmp_path):
    git(shipped, "tag", "v1.0.1")  # HEAD already tagged: the release step is skipped
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_PREFLIGHT=LEDGER_REFUSES)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_hung_preflight_is_bounded_and_refused(shipped, tmp_path):
    before = git(shipped, "ls-remote", "origin")
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_PREFLIGHT="sleep 60", GOH_SHIP_TIMEOUT="2")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "timed out after 2s" in r.stderr, r.stderr  # the bound, not the sleep, ended it
    assert git(shipped, "ls-remote", "origin") == before
