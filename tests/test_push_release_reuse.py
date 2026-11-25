"""The push gate's release rule: a release-only commit on a land-verified parent is gated reduced.

GOH.SHIPRELREUSE R3 (coordinator ruling 2026-11-15). The release kit lands its commit with
lib/release_gate.py's reduced gate, then `git push origin main` ran the whole push gate over it
again, ~10 min, over bytes the tip's land had judged. A repo declaring GOH_PUSH_RELEASE_REUSE=on
now has gates/push_gate.sh check such a commit with the same set the land ran (the structural gate
and the tests that read the release files) when ALL hold: its parent's land was green (tools/land.sh
recorded it in goh-landed), and the diff parent..commit is release files only (release_gate.py's one
definition). Everything else runs the full gate, and the push says why.

THE DECISION is `release_gate.decide_push`, asked in process over a land's record as tools/land.sh
writes it; the push gate runs where what it runs is the subject: the reduced set after a real land,
the release commit's own land, the `landed` sample green and red (GOH.PYSLOWTESTS: each decision was
a whole land and one or two whole pushes, 277 s of a run).
"""

from __future__ import annotations

import functools
import subprocess
import sys
from pathlib import Path

from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_reuse import recorded_hows
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import release_gate  # lib/, after its path
import reuse_audit

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


@functools.cache
def _full_identity() -> str:
    """The full gate's identity as a push's own process computes it."""
    argv = [sys.executable, str(REPO_ROOT / "lib" / "gate_identity.py"), "key", "--mode", "full"]
    out = subprocess.run(argv, env=hermetic_env(drop_git=True), capture_output=True, text=True,
                         check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    return out.stdout.strip()


def _landed(wt: Path, monkeypatch=None) -> str:
    """A green land of WT's tip: tools/land.sh's record of it (the real one is
    test_a_release_only_commit_..._reduced's). With MONKEYPATCH, in process too: the identity this
    process decides by is the record's, as a push's is."""
    sha = git(wt, "rev-parse", "HEAD").strip()
    common = git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    with (Path(common) / "goh-landed").open("a") as f:
        f.write(f"{sha} {_full_identity()}\n")
    if monkeypatch is not None:
        monkeypatch.setattr(release_gate.gate_identity, "key", lambda mode, *a: _full_identity())
    return sha


def _decides(wt: Path, word: str, reason: str) -> None:
    got = release_gate.decide_push(wt)
    assert got[0] == word and got[1].startswith(reason), got


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


def test_a_release_only_commit_on_a_land_verified_parent_is_gated_reduced(repo, tmp_path) -> None:
    """A real land's record, a real push. No CI shadow is queued, whatever the remote (v0.139.6 and
    REUSEOFFLANDED4: a red only hosted CI saw turned `landed` off)."""
    wt = _fixture(repo, tmp_path)
    git(repo, "remote", "add", "origin", "https://github.com/o/n.git")
    parent = git(wt, "rev-parse", "HEAD").strip()
    landed = _land(wt, None)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    common = Path(git(wt, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    record = (common / "goh-landed").read_text().splitlines()[-1]
    assert record == f"{parent} {_full_identity()}"  # what `_landed` writes for the others
    sha = _release(wt)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    assert ran == ["structural", "pytest"], (ran, out)
    assert f"release files only since {parent[:8]}" in out, out
    # Its own verdict: the push records the commit green, under the release gate's identity, never
    # the full gate's (lib/reuse.py): a ship asking for a full verdict on its tree misses.
    assert recorded_hows(tmp_path / "reuse", sha) == ["release"], out
    assert not list((tmp_path / "reuse" / "audit").glob("pending/*.json")), "a CI shadow queued"


def test_a_diff_of_any_other_file_takes_the_full_gate(repo, tmp_path, monkeypatch) -> None:
    """A source file, and a release file with a test that reads it."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    wt = _fixture(repo, tmp_path)
    parent = _landed(wt, monkeypatch)
    for rel in ("src.py", "tests/test_reads.py"):
        _release(wt, rel)
        _decides(wt, "full", f"{rel} is not a release file")
        git(wt, "reset", "-q", "--hard", parent)
    _release(wt)
    _decides(wt, "release", parent)  # the same release without it


def test_a_parent_with_no_green_full_land_takes_the_full_gate(repo, tmp_path, monkeypatch) -> None:
    """No land judged it; its land was red, which records nothing; its land ran a hand-set gate
    (GOH.VERDICTKEY), recorded under another identity. Both lands are real."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setattr(release_gate.gate_identity, "key", lambda mode, *a: _full_identity())
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    none = f"its parent {parent[:8]} has no green land verdict"
    _release(wt)
    _decides(wt, "full", none)
    git(wt, "reset", "-q", "--hard", parent)
    red = _land(wt, "false")
    assert red.returncode != 0 and "gate failed" in red.stderr, red.stdout + red.stderr
    _release(wt)
    _decides(wt, "full", none)
    git(wt, "reset", "-q", "--hard", parent)
    assert _land(wt, "true").returncode == 0
    _release(wt)
    _decides(wt, "full", f"its parent {parent[:8]}'s land verdict is not the full gate's")


def _base(root: Path, tmp_path: Path, decided: str) -> tuple[str, str]:
    """lib/push_runs.sh push_release_base over ROOT when push-decide answers DECIDED (shell)."""
    stub = tmp_path / "decide"
    stub.write_text(f"#!/bin/sh\n{decided}\n")
    stub.chmod(0o755)
    script = (f'. "{REPO_ROOT}/tui/lib.sh"; GOH="{REPO_ROOT}"; . "$GOH/lib/push_runs.sh"; '
              f'GOH_PY={stub}; push_release_base "{root}" "{root}"')  # fmt: skip
    r = subprocess.run(["bash", "-c", script], env=hermetic_env(drop_git=True), capture_output=True,
                       text=True, timeout=patience(30), stdin=subprocess.DEVNULL,
                       check=True)  # fmt: skip
    return r.stdout, r.stderr


def test_the_push_reads_the_decision_where_the_repo_declares_the_rule(tmp_path) -> None:
    """The parent of a reduced push, the sample's two words, and every other answer the full gate,
    its reason said -- a decision that cannot run too. A repo that does not declare the rule is
    never asked, and is told nothing."""
    write_gatesrc(tmp_path, "GOH_PUSH_RELEASE_REUSE=on\n")
    assert _base(tmp_path, tmp_path, "echo release abc") == ("abc\n", "")
    out, said = _base(tmp_path, tmp_path, "echo shadow abcdefgh12")
    assert out == "shadow abcdefgh12\n" and "judged against abcdefgh" in said
    out, said = _base(tmp_path, tmp_path, "echo full because of x")
    assert out == "" and "pre-push: the full gate: because of x" in said
    out, said = _base(tmp_path, tmp_path, "exit 3")
    assert out == "" and "the release rule could not decide" in said
    write_gatesrc(tmp_path, "")
    assert _base(tmp_path, tmp_path, f"touch {tmp_path}/asked; echo release abc") == ("", "")
    assert not (tmp_path / "asked").exists()


def test_the_land_verdicts_reuse_is_the_full_gate_while_audited_off_and_reduced_once_enabled(
    repo, tmp_path, monkeypatch
) -> None:
    """GOH.REUSEOFFLANDED2: with `landed` off every ship's release step ran the full push gate
    (v0.139.3: 933 s, "the audit turned the land verdict's reuse off"). Off is the full gate and
    says so; `enable` with its fix and test gives the release step the land verdict back."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # nothing filed anywhere
    wt = _fixture(repo, tmp_path)
    parent = _landed(wt, monkeypatch)
    _release(wt)
    fields = {"repo": "/r/.git", "sha": "a" * 40, "identity": "i" * 64}
    reuse_audit.compare("landed", fields, "green", "red", lambda: ("green", "red"))
    assert reuse_audit.state("landed")["state"] == "off"
    _decides(wt, "full", "the audit turned the land verdict's reuse off")
    reuse_audit.enable("landed", root=wt, fix="HEAD", test="tests/test_reads.py::test_reads")
    _decides(wt, "release", parent)


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
    parent = _landed(wt, monkeypatch)
    _release(wt)
    _due(monkeypatch, tmp_path, {"repo": "/x/.git", "sha": "b" * 40, "identity": "j" * 64})
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0 and ran == ["full", "shape-ci"], (ran, out)
    assert f"the `landed` sample: the full gate in the land's shape, judged against {parent[:8]}" \
        in out, out  # fmt: skip
    rec = reuse_audit.state("landed")
    assert rec["state"] == "on" and rec["window"]["agreed"] == 1, rec
    _decides(wt, "release", parent)  # the next release reuses again


def test_a_repeated_local_shadow_red_turns_landed_off(repo, tmp_path, monkeypatch) -> None:
    """The sampled full gate red, and red again on its re-run: the land verdict's reuse is off, the
    push refused, and the next release push takes the full gate (in the box's shape) and says so."""
    wt = _fixture(repo, tmp_path)
    parent = _landed(wt, monkeypatch)
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
    assert "pre-push: the full gate: the audit turned the land verdict's reuse off" in out, out


def test_the_landed_path_reads_no_hosted_ci() -> None:
    """A CI red is the loop's fault item (`loopctl reconcile`), never a local switch."""
    assert not hasattr(reuse_audit, "drain"), "the audit still reads hosted CI"
    assert reuse_audit.reuse.PATHS["landed"].shadow == "inline"


def _release_land(wt: Path, tmp_path: Path, parent: str) -> subprocess.CompletedProcess[str]:
    """The release commit's own land, as tools/release_goh.sh makes it: GOH_LAND_GATE is
    release_gate.py's reduced set since the tip the release was cut from, over the push's store."""
    gate = (
        f"{sys.executable} {REPO_ROOT / 'lib' / 'release_gate.py'} run --base {parent} --full false"
    )
    env = hermetic_env(drop_git=True, GATE_REPORT=str(tmp_path / "land-report.txt"),
                       GOH_LAND_GATE=gate, GOH_REUSE_DIR=str(tmp_path / "reuse"))  # fmt: skip
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_release_push_takes_the_release_sets_green_from_its_own_land_on_the_same_tree(
    repo, tmp_path
) -> None:
    """GOH.SHIPSLOW01410: the release land ran the reduced set on the release commit, and its push
    ran the SAME set on the SAME commit again (v0.140.0: 28.9 s land, 29.4 s push; with the text
    judges of GOH.REUSEOFFLANDED5 about 80 s each). The set's green is keyed on the tree, the base
    and the gates' identity (lib/release_gate.py, the `relset` path): the push finds it, runs
    nothing, and still records the commit green under the release gate's identity."""
    wt = _fixture(repo, tmp_path)
    parent = _landed(wt)
    sha = _release(wt)
    landed = _release_land(wt, tmp_path, parent)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    land_ran = (tmp_path / "land-report.txt").read_text().split()
    assert land_ran == ["structural", "pytest"], (land_ran, landed.stdout)
    rc, ran, out = _push(wt, tmp_path)
    assert rc == 0, out
    assert ran == [], (ran, out)
    assert f"the release set passed on this tree since {parent[:8]}" in out, out
    assert recorded_hows(tmp_path / "reuse", sha) == ["release"], out


def test_the_sampled_relset_use_runs_the_set_again_and_the_audit_compares(
    repo, tmp_path, monkeypatch
) -> None:
    """One `relset` hit in `every` is the audit's sample: the push runs the set anyway and the audit
    counts the agreement; a skip nobody re-runs would never be found wrong."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))  # nothing filed anywhere
    monkeypatch.setenv("GATE_REPORT", str(tmp_path / "report.txt"))
    wt = _fixture(repo, tmp_path)
    parent = git(wt, "rev-parse", "HEAD").strip()
    _release(wt)

    def run() -> list[str]:
        (tmp_path / "report.txt").unlink(missing_ok=True)
        pytest_here = f"{sys.executable} -m pytest -q -p no:cacheprovider"
        full, structural = "false", release_gate.STRUCTURAL
        assert release_gate.run(wt, parent, full, structural, pytest_here) == 0
        report = tmp_path / "report.txt"
        return report.read_text().split() if report.exists() else []

    assert run() == ["structural", "pytest"]  # the release land's run, recorded
    assert run() == []  # its push's
    every = reuse_audit.reuse.PATHS["relset"].every
    fields = dict.fromkeys(reuse_audit.reuse.PATHS["relset"].fields, "x")
    left = every - 2  # ... the use above was the window's first
    assert set(reuse_audit.use_many("relset", [fields] * left)) <= {"reuse"}
    assert run() == ["structural", "pytest"]
    rec = reuse_audit.state("relset")
    assert rec["state"] == "on" and rec["window"]["agreed"] == 1, rec
