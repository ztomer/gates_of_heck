"""The push gate's release rule: a release-only commit on a land-verified parent is gated reduced.

GOH.SHIPRELREUSE R3 (coordinator ruling 2026-11-15). The release kit lands its commit with
lib/release_gate.py's reduced gate, then `git push origin main` ran the whole push gate over it
again, ~10 min, over bytes the tip's land had judged. A repo declaring GOH_PUSH_RELEASE_REUSE=on
now has gates/push_gate.sh check such a commit with the same set the land ran (the structural gate
and the tests that read the release files) when ALL hold: its parent's land was green (tools/land.sh
recorded it in goh-landed), and the diff parent..commit is release files only (release_gate.py's one
definition). Everything else runs the full gate, and the push says why. Real lands make every
verdict here: a green one records the parent, a red one records nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"
LAND = REPO_ROOT / "tools" / "land.sh"
ZERO = "0" * 40
REPORT = 'printf "%s\\n" {word} >>"$GATE_REPORT"\n'
RELEASE = "# CHANGELOG\n\n## v0.2.0\n\n* **X.** x\n"


def _fixture(repo: Path, tmp_path: Path, *, opt_in: bool = True) -> Path:
    """The main checkout `repo` with a lane worktree whose tip P carries the gates the push runs."""
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\n" + REPORT.format(word="full"))
    (repo / "gates").mkdir()
    (repo / "gates" / "structural.sh").write_text(
        "#!/usr/bin/env bash\n" + REPORT.format(word="structural")
    )
    (repo / "gates" / "structural.sh").chmod(0o755)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_reads.py").write_text(
        "import os\n\n# reads CHANGELOG.md\n\n\ndef test_reads():\n"
        '    with open(os.environ["GATE_REPORT"], "a") as f:\n        f.write("pytest\\n")\n'
    )
    (repo / "tests" / "test_other.py").write_text("def test_other():\n    raise SystemExit(9)\n")
    (repo / "CHANGELOG.md").write_text("# CHANGELOG\n\n## Unreleased\n")
    (repo / "changelog").mkdir()
    (repo / "changelog" / "X.md").write_text("* **X.** x\n")
    (repo / "src.py").write_text("A = 1\n")
    write_gatesrc(repo, "GOH_PUSH_RELEASE_REUSE=on\n" if opt_in else "")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "the fixture")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / "src.py").write_text("A = 2\n")
    git(wt, "commit", "-q", "--no-verify", "-am", "P: the change a land judges")
    return wt


def _land(wt: Path, gate: str | None) -> subprocess.CompletedProcess[str]:
    """A real land: `gate` None is the full gate (the kit's own push gate over the fixture, whose
    tools/gate.sh reports `full` to a file of its own); a string is a hand-set GOH_LAND_GATE."""
    over = {} if gate is None else {"GOH_LAND_GATE": gate}
    env = hermetic_env(drop_git=True, GATE_REPORT=str(wt.parent / "land-report.txt"), **over)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _release(wt: Path, *extra: str) -> str:
    """R: the release commit on top of P -- the changelog cut, plus any `extra` file touched."""
    (wt / "CHANGELOG.md").write_text(RELEASE)
    (wt / "changelog" / "X.md").unlink()
    for rel in extra:
        with (wt / rel).open("a") as f:
            f.write("# touched\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "--no-verify", "-m", "release: v0.2.0")
    return git(wt, "rev-parse", "HEAD").strip()


def _push(wt: Path, tmp_path: Path) -> tuple[int, list[str], str]:
    sha = git(wt, "rev-parse", "HEAD").strip()
    report = tmp_path / "report.txt"
    report.unlink(missing_ok=True)
    verdicts = tmp_path / "verdicts.txt"
    env = hermetic_env(
        drop_git=True, GATE_REPORT=str(report), GOH_GATE_VERDICTS=str(verdicts),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"), GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
    )  # fmt: skip
    p = subprocess.run(
        ["bash", str(PUSH_GATE)], cwd=wt, env=env, text=True, capture_output=True,
        input=f"{sha} {sha} refs/heads/main {ZERO}\n", timeout=patience(300), check=False,
    )  # fmt: skip
    ran = report.read_text().split() if report.exists() else []
    return p.returncode, ran, p.stdout + p.stderr


def _full(ran: list[str], out: str, reason: str) -> None:
    assert ran[:1] == ["full"] and "pytest" not in ran, (ran, out)
    assert f"pre-push: the full gate: {reason}" in out, out


def test_a_release_only_commit_on_a_land_verified_parent_is_gated_reduced(repo, tmp_path) -> None:
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    landed = _land(wt, None)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    sha = _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    assert ran == ["structural", "pytest"], (ran, out)
    assert f"release files only since {parent[:8]}" in out, out
    # Its own verdict: the push records the commit green, as for a full gate (GOH_GATE_VERDICTS).
    assert f"green {sha}" in (tmp_path / "verdicts.txt").read_text(), out


def test_a_non_release_file_in_the_diff_takes_the_full_gate(repo, tmp_path) -> None:
    wt = _fixture(repo, tmp_path)
    assert _land(wt, None).returncode == 0
    _release(wt, "src.py")
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, "src.py is not a release file")


def test_a_release_file_with_a_test_that_reads_it_takes_the_full_gate(repo, tmp_path) -> None:
    wt = _fixture(repo, tmp_path)
    assert _land(wt, None).returncode == 0
    _release(wt, "tests/test_reads.py")
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, "tests/test_reads.py is not a release file")


def test_a_parent_no_land_judged_takes_the_full_gate(repo, tmp_path) -> None:
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, f"its parent {parent[:8]} has no green land verdict")


def test_a_parent_whose_land_was_red_takes_the_full_gate(repo, tmp_path) -> None:
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    red = _land(wt, "false")
    assert red.returncode != 0 and "gate failed" in red.stderr, red.stdout + red.stderr
    _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, f"its parent {parent[:8]} has no green land verdict")


def test_a_repo_that_does_not_declare_the_rule_runs_the_full_gate_and_says_nothing(
    repo, tmp_path
) -> None:
    wt = _fixture(repo, tmp_path, opt_in=False)
    assert _land(wt, None).returncode == 0
    _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran[:1] == ["full"], (ran, out)
    assert "release files only" not in out and "the full gate:" not in out, out


def test_a_parent_landed_under_a_hand_set_gate_takes_the_full_gate(repo, tmp_path) -> None:
    """GOH.VERDICTKEY: GOH_LAND_GATE=true lands and records the parent, under another identity."""
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    assert _land(wt, "true").returncode == 0
    _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, f"its parent {parent[:8]}'s land verdict is not the full gate's")
