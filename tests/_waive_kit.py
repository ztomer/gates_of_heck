"""A planted main and lane for the waiver's tests (tests/test_main_red_waive.py,
tests/test_rule_inflight.py): a main checkout whose suite loads tests/_land_waive.py, a lane
worktree beside it, and a nested pytest run of the lane's tree under a GOH_LAND_WAIVE the lander's
own code (lib/land_waive.py `words`) computed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

CONFTEST = f"""import sys
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
pytest_plugins = ["_land_waive"]
"""


def put(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo: Path, msg: str = "c") -> str:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").strip()


def main_repo(m: Path, files: dict[str, str]) -> tuple[Path, str]:
    """M, conftest's `repo` (a clone of the shared template, on main), made the planted main."""
    put(m, "pytest.ini", "[pytest]\n")
    put(m, "tests/conftest.py", CONFTEST)
    for rel, text in files.items():
        put(m, rel, text)
    return m, commit(m, "base")


def lane(m: Path, at: str, name: str = "lane") -> Path:
    wt = m.parent / name
    git(m, "worktree", "add", "-q", "-b", name, str(wt), at)
    return wt


def suite(wt: Path, waive: list[str], *extra: str) -> subprocess.CompletedProcess:
    """The lane tree's pytest under the lander's GOH_LAND_WAIVE (WAIVE: `words`' output)."""
    env = hermetic_env(**dict(w.split("=", 1) for w in waive))
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "-p", "no:randomly", *extra, "tests"], cwd=wt, env=env,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(300), check=False)  # fmt: skip
