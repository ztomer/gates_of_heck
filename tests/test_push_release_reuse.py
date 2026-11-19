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
import sys
from pathlib import Path

from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_reuse import recorded_hows
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse_audit  # lib/, after its path

PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"
LAND = REPO_ROOT / "tools" / "land.sh"
ZERO = "0" * 40
REPORT = 'printf "%s\\n" {word} >>"$GATE_REPORT"\n'
RELEASE = "# CHANGELOG\n\n## v0.2.0\n\n* **X.** x\n"
# A fixture test that reads ITS repo's changelog, spelled apart so goh's own reader table
# (tools/affected_tests.py readers) does not take this file for a reader of goh's.
READS_CHANGELOG = 'TEXT = (REPO_ROOT / "CHANGELOG' + '.md").read_text()\n'
# The full gate says the suite shape it ran in (tests/suite_home.py SHAPE): a land's is `ci`, so the
# `landed` shadow's fresh full gate must be too (GOH.LANDEDREUSEON); a plain push's is the box's.
SHAPE = "GATES_OF_HECK_SUITE_SHAPE"
SHAPE_WORD = '"shape-${' + SHAPE + ':-box}"'


def _fixture(repo: Path, tmp_path: Path, *, opt_in: bool = True) -> Path:
    """The main checkout `repo` with a lane worktree whose tip P carries the gates the push runs."""
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(
        "#!/usr/bin/env bash\n" + REPORT.format(word="full") + REPORT.format(word=SHAPE_WORD)
        + '[ ! -e "${GATE_REPORT%/*}/fail" ] || exit 1\n'
    )  # fmt: skip
    (repo / "gates").mkdir()
    (repo / "gates" / "structural.sh").write_text(
        "#!/usr/bin/env bash\n" + REPORT.format(word="structural")
    )
    (repo / "gates" / "structural.sh").chmod(0o755)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_reads.py").write_text(
        "import os\nfrom pathlib import Path\n\nREPO_ROOT = Path(__file__).parent.parent\n"
        + READS_CHANGELOG
        + "\n\n"
        "def test_reads():\n"
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
    env = hermetic_env(
        drop_git=True, GATE_REPORT=str(report), GOH_REUSE_DIR=str(tmp_path / "reuse"),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"), GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
    )  # fmt: skip
    env.pop(SHAPE, None)  # this suite's own shape is no push's: the push gate names the shadow's
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
    # Its own verdict: the push records the commit green, under the release gate's identity, never
    # the full gate's (lib/reuse.py): a ship asking for a full verdict on its tree misses.
    assert recorded_hows(tmp_path / "reuse", sha) == ["release"], out


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


def test_the_land_verdicts_reuse_is_the_full_gate_while_audited_off_and_reduced_once_enabled(
    repo, tmp_path, monkeypatch
) -> None:
    """GOH.REUSEOFFLANDED2: with `landed` off every ship's release step ran the full push gate
    (v0.139.3: 933 s, "the audit turned the land verdict's reuse off"). Off is the full gate and
    says so; `enable` with its fix and test gives the release step the land verdict back."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # nothing filed anywhere
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    assert _land(wt, None).returncode == 0
    _release(wt)
    fields = {"repo": "/r/.git", "sha": "a" * 40, "identity": "i" * 64}
    reuse_audit.compare("landed", fields, "green", "red", lambda: ("green", "red"))
    assert reuse_audit.state("landed")["state"] == "off"
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    _full(ran, out, "the audit turned the land verdict's reuse off")
    reuse_audit.enable("landed", root=wt, fix="HEAD", test="tests/test_reads.py::test_reads")
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran == ["structural", "pytest"], (ran, out)
    assert f"release files only since {parent[:8]}" in out, out


def _due(monkeypatch, tmp_path: Path, fields: dict[str, str]) -> None:
    """The next `landed` use is its window's sampled one: every-1 uses of the path counted first."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # nothing filed anywhere
    every = reuse_audit.reuse.PATHS["landed"].every
    assert every > 1, "a release push's every reuse sampled is a full gate on every ship"
    assert set(reuse_audit.use_many("landed", [fields] * (every - 1))) == {"reuse"}


def test_the_sampled_release_push_runs_the_full_gate_in_the_lands_shape_and_compares(
    repo, tmp_path, monkeypatch
) -> None:
    """GOH.LANDEDREUSEON: `landed`'s fresh side was hosted CI, another machine's shape, so a
    CI-only red switched a local reuse off. It is a LOCAL full gate in the land's `ci` shape, made
    at the push on one release in `every`, and compared with the land's green."""
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    assert _land(wt, None).returncode == 0
    _release(wt)
    _due(monkeypatch, tmp_path, {"repo": "/x/.git", "sha": "b" * 40, "identity": "j" * 64})
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran == ["full", "shape-ci"], (ran, out)
    assert f"the `landed` sample: the full gate in the land's shape, judged against {parent[:8]}" \
        in out, out  # fmt: skip
    rec = reuse_audit.state("landed")
    assert rec["state"] == "on" and rec["window"]["agreed"] == 1, rec
    rc, ran, out = _push(wt, tmp_path)  # the next release reuses again
    assert rc == 0 and ran == ["structural", "pytest"], (ran, out)


def test_a_repeated_local_shadow_red_turns_landed_off(repo, tmp_path, monkeypatch) -> None:
    """The sampled full gate red, and red again on its re-run: the land verdict's reuse is off, the
    push refused, and the next release push takes the full gate (in the box's shape) and says so."""
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    assert _land(wt, None).returncode == 0
    _release(wt)
    _due(monkeypatch, tmp_path, {"repo": "/x/.git", "sha": "b" * 40, "identity": "j" * 64})
    (tmp_path / "fail").write_text("")
    rc, ran, out = _push(wt, tmp_path)
    assert rc != 0 and ran == ["full", "shape-ci"] * 2, (ran, out)
    rec = reuse_audit.state("landed")
    assert rec["state"] == "off" and rec["cause"] == "disagree", rec
    ev = rec["evidence"]
    assert (ev["reused"], ev["fresh"]) == ("green", "red"), ev
    assert ev["again"] == {"reused": "green", "fresh": "red"}, ev
    assert ev["fields"]["sha"] == parent and "local full gate" in ev["fresh_from"], ev
    (tmp_path / "fail").unlink()
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran == ["full", "shape-box"], (ran, out)
    _full(ran, out, "the audit turned the land verdict's reuse off")


def test_a_ci_only_red_never_reaches_the_landed_path(repo, tmp_path, monkeypatch) -> None:
    """v0.139.6 and REUSEOFFLANDED4: a red only hosted CI saw turned `landed` off, though CI is
    advisory and the box's land judged the parent. No release push queues a CI shadow and the audit
    reads no CI: a CI red is the loop's fault item (`loopctl reconcile`), never a local switch."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    wt = _fixture(repo, tmp_path)
    git(repo, "remote", "add", "origin", "https://github.com/o/n.git")
    assert _land(wt, None).returncode == 0
    _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran == ["structural", "pytest"], (ran, out)
    assert not list((tmp_path / "reuse" / "audit").glob("pending/*.json")), "a CI shadow queued"
    assert not hasattr(reuse_audit, "drain"), "the audit still reads hosted CI"
    assert reuse_audit.reuse.PATHS["landed"].shadow == "inline"
    assert reuse_audit.state("landed")["state"] == "on"
