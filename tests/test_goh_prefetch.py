"""The native tier starts its delegated checkers together and reports them in order (P1e).

`structural --full` on media_server spent 4.7 of 4.9 s in delegated Python checkers run one after
another. Two things must hold once they overlap, each tested against a fake GOH_DIR whose checkers
only sleep and leave a marker:

* they really do overlap (five 1 s checkers finish in well under 5 s);
* nothing outlives the gate: after a fail-fast red step, every checker already started has
  FINISHED by the time `goh` returns (its marker exists) -- a gate that exits leaving children
  running is the class check_no_unreaped_spawn.py exists for.

Output order and verdicts are pinned separately by test_goh_structural_parity.py.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DELEGATED = [
    "check_no_credential_urls.py",
    "check_md_links.py",
    "check_lock_version.py",
    "check_no_unreaped_spawn.py",
]
PY_STUB = (
    "import os, pathlib, sys, time\n"
    "time.sleep(float(os.environ.get('STUB_SLEEP', '1')))\n"
    "pathlib.Path(os.environ['MARK_DIR'], os.path.basename(sys.argv[0])).touch()\n"
    "print('stub ok')\n"
)


def _fake_goh_dir(tmp: Path, shell_fails: bool) -> Path:
    root = tmp / "goh"
    (root / "checks").mkdir(parents=True)
    (root / "lib").symlink_to(ROOT / "lib")
    (root / "docs").symlink_to(ROOT / "docs")
    for name in DELEGATED:
        (root / "checks" / name).write_text(PY_STUB)
    shell = (
        "#!/bin/bash\necho '✗ planted shell failure' >&2\nexit 1\n"
        if shell_fails
        else '#!/bin/bash\nsleep "${STUB_SLEEP:-1}"\ntouch "$MARK_DIR/check_shell_lint.sh"\n'
    )
    (root / "checks" / "check_shell_lint.sh").write_text(shell)
    return root


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / ".gatesrc").write_text("GOH_MAX_LINES=500\n")
    (repo / "README.md").write_text("# fixture\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    return repo


def _run(goh: Path, tmp: Path, shell_fails: bool, sleep: str):
    marks = tmp / "marks"
    marks.mkdir()
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_")) or k == "GOH_LIVE"
    }
    env.update(GOH_DIR=str(_fake_goh_dir(tmp, shell_fails)), MARK_DIR=str(marks), STUB_SLEEP=sleep)
    t0 = time.monotonic()
    r = subprocess.run(
        [str(goh), "structural", "--staged"],
        cwd=_repo(tmp),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    return r, time.monotonic() - t0, {p.name for p in marks.iterdir()}


def test_delegated_checkers_overlap(goh: Path, tmp_path: Path) -> None:
    r, elapsed, marks = _run(goh, tmp_path, shell_fails=False, sleep="1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert marks == set(DELEGATED) | {"check_shell_lint.sh"}, marks
    assert elapsed < 3.0, f"five 1 s checkers took {elapsed:.1f} s: they ran one after another"


def test_a_red_step_still_joins_every_checker_it_started(goh: Path, tmp_path: Path) -> None:
    r, _, marks = _run(goh, tmp_path, shell_fails=True, sleep="1.5")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "shell lint" in r.stderr and "planted shell failure" in r.stderr, r.stderr
    assert marks == set(DELEGATED), f"checkers still running when goh returned: {marks}"
