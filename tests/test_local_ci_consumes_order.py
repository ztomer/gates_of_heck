"""Under GOH_CI_JOBS>1 a step that `consumes=` a path starts only after the steps that
`produces=` it are done (GOH.PROVENBUILD gap 2).

gates/_steps.sh parsed the declarations for the proven cache and nothing else, so the scheduler
saw only resource tags: a producer held back by a busy tag let its consumer, declared after it,
start first and read nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_local_ci_jobs import run_ci, steps


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


def test_a_consumer_waits_for_its_producer_even_when_the_producer_is_held_back(repo: Path) -> None:
    (repo / "build.sh").write_text("echo built > out.txt\n")
    steps(
        repo,
        "[t] sleep 1",  # holds tag t, so the producer cannot start yet
        "[t,produces=out.txt] sh build.sh",
        "[consumes=out.txt] test -f out.txt",
        extra="GOH_CI_JOBS=3\n",
    )
    r, _ = run_ci(repo)
    assert r.returncode == 0, f"the consumer ran before its producer:\n{r.stdout}{r.stderr}"


def test_a_consumer_of_nothing_produced_is_not_held(repo: Path) -> None:
    steps(
        repo,
        "[t] sleep 0.2",
        "[consumes=given.txt] true",
        extra="GOH_CI_JOBS=2\n",
    )
    (repo / "given.txt").write_text("x\n")
    r, _ = run_ci(repo)
    assert r.returncode == 0, r.stdout + r.stderr
