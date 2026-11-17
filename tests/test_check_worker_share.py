"""checks/check_worker_share.py: a consumer's gate script never sizes a pool to the machine
(GOH.POOLSHARE2). Each case plants the shape in a consumer repo and reads the verdict."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, commit_all, run_gate, write

CHECK = REPO_ROOT / "checks" / "check_worker_share.py"
_spec = importlib.util.spec_from_file_location("check_worker_share", CHECK)
cws = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cws)


def judge(repo, seed=None):
    return cws.judge(repo, seed or {})


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("tools/gate.sh", "python3 -m pytest -n auto -q tests/\n"),
        ("Makefile", "test:\n\t$(PY) -m pytest -q -n auto tests/\n"),
        ("mk/verify.mk", "v:\n\tswift test --skip-build --parallel --scratch-path x\n"),
        ("tools/suite.py", 'CMD = ["swift", "test", "--skip-build", "--parallel"]\n'),
        (".gatesrc", "GOH_CI_STEPS='python3 -m pytest tools/tests -q -n auto'\n"),
        ("tools/build.sh", 'make -j"$(sysctl -n hw.logicalcpu)" app\n'),
    ],
)
def test_a_planted_machine_sized_pool_in_a_consumer_gate_script_is_red(repo, rel, body):
    write(repo, rel, body)
    commit_all(repo)
    assert judge(repo) == 1


@pytest.mark.parametrize(
    ("rel", "body"),
    [
        ("tools/gate.sh", 'python3 -m pytest -n "$(goh_workers)" -q tests/\n'),
        ("mk/verify.mk", "v:\n\tswift test --parallel --num-workers $$(w) --skip-build\n"),
        ("tools/probe.py", "READ = ['footprint', '-j', str(pid)]  # JSON, not jobs\n"),
        ("tools/tests/test_x.py", 'LINE = "pytest -n auto"\n'),  # a test spells the shape
        ("src/run.sh", "pytest -n auto\n"),  # not a gate script
        ("tools/gate.sh", "# pytest -n auto was every core\n"),  # prose
    ],
)
def test_a_bounded_pool_or_a_file_outside_the_scope_is_green(repo, rel, body):
    write(repo, rel, body)
    commit_all(repo)
    assert judge(repo) == 0


def test_a_marker_with_a_reason_excuses_and_a_bare_one_does_not(repo):
    write(repo, "tools/t.sh", "# workers-ok: the denominator of a load reading\nc=$(nproc)\n")
    commit_all(repo)
    assert judge(repo) == 0
    write(repo, "tools/t.sh", "# workers-ok:\nc=$(nproc)\n")
    commit_all(repo)
    assert judge(repo) == 1


def test_the_seed_holds_a_count_and_a_file_over_it_is_red(repo, capsys):
    write(repo, "tools/a.sh", "pytest -n auto\n")
    commit_all(repo)
    name = cws.repo_name(repo)
    assert judge(repo, {name: {"tools/a.sh": 1}}) == 0
    write(repo, "tools/a.sh", "pytest -n auto\nmake -j\n")
    commit_all(repo)
    assert judge(repo, {name: {"tools/a.sh": 1}}) == 1
    write(repo, "tools/a.sh", "true\n")
    commit_all(repo)
    capsys.readouterr()
    assert judge(repo, {name: {"tools/a.sh": 1}}) == 0
    assert "lower the entry" in "".join(capsys.readouterr())


def test_the_seed_is_keyed_by_the_origins_name_not_the_worktree_dir(repo):
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:o/CadGoose.git"],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert cws.repo_name(repo) == "CadGoose"


def test_the_seed_only_names_files_and_positive_counts():
    data = json.loads((REPO_ROOT / "checks" / "worker_share_seed.json").read_text())
    for repo, files in data.items():
        if repo.startswith("_"):
            continue
        assert files and all(isinstance(n, int) and n > 0 for n in files.values()), repo
        assert all(cws.in_scope(rel) for rel in files), repo


def test_the_probe_is_green():
    r = subprocess.run(
        [sys.executable, str(CHECK), "--probe"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_structural_full_refuses_a_consumer_that_plants_xdist_auto(repo):
    """The estate-wide half: the push gate every consumer runs carries the check."""
    write(repo, "tools/gate.sh", "#!/bin/bash\npython3 -m pytest -n auto -q\n")
    commit_all(repo)
    r = run_gate(repo, "gates/structural.sh", "--full")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "tools/gate.sh:2: a pool sized to the machine" in out, out


def test_a_private_repo_is_seeded_by_its_first_commit_not_its_name(repo):
    """A public tree may not name a private repo: its seed entry is `root:<first commit>`."""
    write(repo, "tools/a.sh", "pytest -n auto\n")
    commit_all(repo)
    assert judge(repo, {cws.root_commit(repo): {"tools/a.sh": 1}}) == 0
    assert cws.root_commit(repo).startswith("root:")
