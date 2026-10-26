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
                "lib/house_python.sh", "gates/required_tools.tsv"):  # fmt: skip
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
    return subprocess.run(
        ["bash", str(goh / "tools" / "gate_profile.sh"), str(repo)],
        # hermetic: under a real profile (this suite runs inside one) an inherited GOH_TIMINGS
        # put this fixture's control rows into the outer run's file, and its shell control mean
        # with them (5.3, goh's own push profile).
        env=hermetic_env(),
        capture_output=True, text=True, check=False, timeout=120, stdin=subprocess.DEVNULL,
    )  # fmt: skip


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
