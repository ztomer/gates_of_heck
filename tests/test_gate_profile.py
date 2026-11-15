"""gate_profile.sh says WHY the push gate failed, not only that it did.

land.sh runs it as its gate. On 2026-10-11 it refused a tip with a timing table and a log path and
nothing else; the two census tests that had failed were three files away. The push gate's own
failure lines are in the log the profile writes, so a red profile prints them.
"""

import shutil
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, git, hermetic_env


def _goh_copy(tmp_path: Path, push_gate: str) -> Path:
    """The files gate_profile.sh runs, outside any checkout (so they run as they are), with the
    push gate replaced."""
    goh = tmp_path / "goh"
    for rel in ("tools/gate_profile.sh", "gates/_from_head.sh", "gates/_git_env.sh",
                "lib/step_timings.py", "lib/step_control.py", "lib/fail_lines.py",
                "lib/house_python.sh", "gates/required_tools.tsv",
                "lib/log_follow.py", "lib/bash5.sh"):  # fmt: skip
        (goh / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, goh / rel)
    (goh / "gates" / "push_gate.sh").write_text(push_gate)
    return goh


def _profile(tmp_path: Path, push_gate: str) -> subprocess.CompletedProcess[str]:
    goh = _goh_copy(tmp_path, push_gate)
    repo = tmp_path / "repo"
    fast_init(repo, "main")
    git(
        repo,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "c",
    )
    r = subprocess.run(
        ["bash", str(goh / "tools" / "gate_profile.sh"), str(repo)],
        # hermetic: under a real profile (this suite runs inside one) an inherited GOH_TIMINGS
        # put this fixture's control rows into the outer run's file, and its shell control mean
        # with them (5.3, goh's own push profile).
        env=hermetic_env(),
        capture_output=True, text=True, check=False, timeout=120, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    _assert_whole(r.stdout + r.stderr)
    return r


def _assert_whole(out: str) -> None:
    """The copy runs every file the profile sources (GOH.PROFILEFIXTURE): one it lacked printed
    `lib/bash5.sh: No such file or directory` and the profile ran on without its bash guard."""
    assert "No such file" not in out and "command not found" not in out, out


def test_a_red_push_gate_prints_the_lines_that_name_the_failure(tmp_path: Path) -> None:
    r = _profile(
        tmp_path,
        "cat >/dev/null\necho 'lots of passing noise'\n"
        "echo 'FAILED tests/test_x.py::test_the_real_reason'\n"
        "echo '✗ pre-push: the gate failed -- nothing pushed'\nexit 1\n",
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "FAILED tests/test_x.py::test_the_real_reason" in r.stderr, r.stderr
    assert "lots of passing noise" not in r.stderr, r.stderr


def test_a_green_push_gate_prints_no_failure_lines(tmp_path: Path) -> None:
    r = _profile(tmp_path, "cat >/dev/null\necho 'all good'\nexit 0\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "failure lines" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_profile_inside_a_profile_writes_nothing_into_the_outer_one(
    tmp_path: Path, monkeypatch
) -> None:
    outer = tmp_path / "outer.jsonl"
    monkeypatch.setenv("GOH_TIMINGS", str(outer))
    r = _profile(tmp_path, "cat >/dev/null\nexit 0\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not outer.exists() or not outer.read_text().strip(), outer.read_text()


# The push gate below waits for the TEST to release it, and the test releases it only on seeing the
# gate's first step line on the profile's stdout. Captured whole into the log (the old shape), that
# line surfaces only after the gate ends, so the gate is never released and fails (GOH.LANDPROGRESS:
# a 40 min land under load read as silence).
HELD_GATE = """cat >/dev/null
echo '→ [1/2] (.gatesrc) ./step_one.sh'
echo '✓ [1/2] ./step_one.sh'
echo 'noise from inside the step'
for _ in $(seq 1 300); do [ -f "$RELEASE" ] && break; sleep 0.1; done
[ -f "$RELEASE" ] || { echo '✗ [2/2] FAILED: nobody saw step one before the gate ended'; exit 1; }
echo '✓ [2/2] ./step_two.sh'
exit 0
"""


def test_each_step_line_is_shown_while_the_gate_runs(tmp_path: Path) -> None:
    goh = _goh_copy(tmp_path, HELD_GATE)
    repo = tmp_path / "repo"
    fast_init(repo, "main")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
        "-m", "c")  # fmt: skip
    release = tmp_path / "release"
    env = {**hermetic_env(), "RELEASE": str(release)}
    proc = subprocess.Popen(
        ["bash", str(goh / "tools" / "gate_profile.sh"), str(repo)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    seen: list[str] = []
    try:
        for line in proc.stdout:
            seen.append(line)
            if "✓ [1/2] ./step_one.sh" in line:
                release.touch()
        rc = proc.wait(timeout=120)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    out = "".join(seen)
    assert rc == 0, out
    _assert_whole(out)
    # The log's path comes FIRST, before any step: a reader can tail it from the start.
    first = next(i for i, ln in enumerate(seen) if "[1/2]" in ln)
    assert any("log:" in ln for ln in seen[:first]), out
    assert "✓ [2/2] ./step_two.sh" in out, out
    assert "noise from inside the step" not in out, out  # step lines only; the log has the rest
