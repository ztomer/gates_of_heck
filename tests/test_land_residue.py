"""A finished land leaves no files of its own behind (GOH.LANDLEAKS).

Found by GOH.BATCHPREFROM on 2026-11-19, three kinds of residue:
  * kept pre-step verdicts in the queue's `pre/` from enqueues whose request is gone (14 files,
    kept before lib/land_prestep.py `discard` existed); nothing reaped them;
  * `goh-land-timings.*` in $TMPDIR/goh.noindex: tools/land.sh removed its timings file and the
    gate's logs only when the file was EMPTY, so every gate that profiled (all of them) left three;
  * a land that runs two gates (main's, then its own) handed both the same GOH_TIMINGS, and
    tools/gate_profile.sh truncated `$out.log` for the second, so the first gate's output was lost.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, git, hermetic_env
from test_gate_profile import _goh_copy  # its copy of the profile's closure
from test_landq_batch import env, landq, lane, recorded  # noqa: F401  (env: a fixture)
from timing_bounds import patience

from lib import land_prestep, log_follow

# A gate that profiles as tools/gate_profile.sh does: timing rows, then its log and a second one.
PROFILING_GATE = ('printf \'{"tier":"step","label":"x","ms":1}\\n\' >>"$GOH_TIMINGS"; '
                  'echo log >"$GOH_TIMINGS.log"; echo two >"$GOH_TIMINGS.2.log"; '
                  ': >"$GOH_TIMINGS.controls.log"')  # fmt: skip


def _goh_left(tmpdir: Path) -> list[str]:
    parent = tmpdir / "goh.noindex"
    return sorted(p.name for p in parent.iterdir()) if parent.is_dir() else []


NOW_NS = 1_795_000_000 * 10**9  # the reap's clock, given: an age is a name, never a wait
HOUR_NS = 3600 * 10**9


def _keep(q: Path, sha: str, born_ns: int) -> Path:
    keep = q / "pre" / f"{sha}-{born_ns}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text(sha + "\n")
    Path(f"{keep}.record").write_text("{}\n")
    return keep


def test_a_verdict_whose_request_is_gone_is_reaped_a_live_one_kept(tmp_path: Path) -> None:
    q = tmp_path / "q"
    orphan = _keep(q, "a" * 40, NOW_NS - 3 * HOUR_NS)
    filed = _keep(q, "b" * 40, NOW_NS - 3 * HOUR_NS)
    young = _keep(q, "c" * 40, NOW_NS - 5 * 10**9)  # an enqueue in its pre-step: no request yet
    (q / "running").mkdir(parents=True)
    (q / "running" / "1-T.B.json").write_text(json.dumps({"pre": {"keep": str(filed)}}))
    stray = q / "pre" / f"{'d' * 40}-1.record"  # a record whose verdict is already gone
    stray.write_text("{}\n")
    gone = land_prestep.reap(q, NOW_NS)
    assert sorted(p.name for p in gone) == sorted(
        [orphan.name, f"{orphan.name}.record", stray.name]
    )
    left = sorted(p.name for p in (q / "pre").iterdir())
    assert left == sorted([filed.name, f"{filed.name}.record", young.name, f"{young.name}.record"])


def test_land_removes_its_timings_and_every_gate_log(repo: Path, tmp_path: Path) -> None:
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    for name, gate, code in (("one", PROFILING_GATE, 0), ("two", PROFILING_GATE + "; false", 1)):
        wt = lane(repo, tmp_path, name, {f"{name}.txt": "1\n"})
        env_ = hermetic_env(drop_git=True, GOH_LAND_GATE=gate, TMPDIR=str(tmpdir))
        r = subprocess.run(["bash", str(REPO_ROOT / "tools" / "land.sh")], cwd=wt, env=env_,
                           capture_output=True, text=True, timeout=patience(120),
                           stdin=subprocess.DEVNULL, check=False)  # fmt: skip
        assert r.returncode == code, r.stdout + r.stderr
        assert [n for n in _goh_left(tmpdir) if n.startswith("goh-land")] == [], (
            gate, r.stdout + r.stderr)  # fmt: skip


def test_a_finished_land_leaves_no_files_of_its_own(
    repo: Path,
    tmp_path: Path,
    env: dict[str, str],  # noqa: F811
) -> None:
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    env = {**env, "GOH_LANDQ_BATCH": "1", "GOH_LAND_GATE": PROFILING_GATE, "TMPDIR": str(tmpdir)}
    q = Path(env["GOH_LANDQ_DIR"])
    _keep(q, "e" * 40, 1000)  # an enqueue's verdict from before discard existed
    wt = lane(repo, tmp_path, "one", {"one.txt": "1\n"})
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.ONE", "--no-start",
                 "--no-pre", "fixture")  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    drained = landq(env, "--drain")
    assert drained.returncode == 0, drained.stdout + drained.stderr
    assert recorded(env)["T.ONE"]["state"] == "landed", recorded(env)
    assert list((q / "pre").iterdir()) == []
    assert [n for n in _goh_left(tmpdir) if n.startswith("goh-land")] == []


def _profile_twice(tmp_path: Path) -> tuple[Path, list[str]]:
    """gate_profile.sh twice on one GOH_TIMINGS, as land.sh's two gates run it."""
    outs = []
    timings = tmp_path / "timings"
    for word, code in (("FIRST", 0), ("SECOND", 1)):
        goh = _goh_copy(
            tmp_path / word, f"cat >/dev/null\necho '{word} gate output'\nexit {code}\n"
        )
        repo = tmp_path / word / "repo"
        fast_init(repo, "main")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
            "-m", "c")  # fmt: skip
        penv = {**hermetic_env(), "GOH_TIMINGS": str(timings)}
        r = subprocess.run(["bash", str(goh / "tools" / "gate_profile.sh"), str(repo)], env=penv,
                           capture_output=True, text=True, check=False, timeout=patience(120),
                           stdin=subprocess.DEVNULL)  # fmt: skip
        outs.append(r.stdout + r.stderr)
    return timings, outs


def test_a_second_gate_on_one_timings_file_keeps_the_first_gate_s_log(tmp_path: Path) -> None:
    timings, outs = _profile_twice(tmp_path)
    logs = sorted(timings.parent.glob(f"{timings.name}*.log"))
    texts = "".join(p.read_text() for p in logs if not p.name.endswith(".controls.log"))
    assert "FIRST gate output" in texts and "SECOND gate output" in texts, (logs, outs)
    # each run names its own log, so the first's path still holds the first's output
    first = outs[0].split("(log: ", 1)[1].split(")", 1)[0]
    assert "FIRST gate output" in Path(first).read_text(), outs[0]


def test_the_follower_carries_the_coverage_phases(capsys) -> None:
    """The land log keeps what land-profile read from the deleted gate log (lib/land_profile.py)."""
    log_follow._show(
        "noise\n  ✓ phase 'workspace: report': 3 s\n  → phase 'x' started 02:28Z\n", ""
    )
    assert capsys.readouterr().out.splitlines() == ["✓ phase 'workspace: report': 3 s"]
