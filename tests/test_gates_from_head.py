"""Consumers run the gates from an immutable export of HEAD, never the shared working tree (C4).

Gate source edited in the shared checkout was LIVE in every consumer the moment it was saved:
2026-10-05 alone, an uncommitted `_goh_bin.sh` reached antiknob's push ahead of the binary it
checks, a calibration that disabled a branch "for a few seconds" turned three of this suite's
tests red in another session, and media_server's pre-push warned on a peer's `_line_cap.sh`. The
warning existed; the cause did not have to. Now every entry point re-runs itself from
`~/.cache/goh/head/<HEAD>` (GOH_HEAD_CACHE), built once per commit and never modified, and a
developer asks for the working tree on purpose with GOH_LIVE=1.

Planted on a scratch clone of the gates: an uncommitted edit that changes a gate's verdict.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_gate_environment import _clean_checkout_of_todays_gates

PLANT = "LIVE-WORKING-TREE-EDIT-5521"


@pytest.fixture(scope="module")
def gates(tmp_path_factory) -> Path:
    return _clean_checkout_of_todays_gates(tmp_path_factory.mktemp("g"))


@pytest.fixture
def dirty(gates: Path):
    """An uncommitted edit to a sourced lib every gate reads, undone afterwards."""
    common = gates / "gates" / "_common.sh"
    before = common.read_text()
    common.write_text(before + f'\necho "{PLANT}" >&2\n')
    yield gates
    common.write_text(before)


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("# consumer\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    return repo


def _env(tmp_path: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_", "PYTEST_"))}
    env["GOH_HEAD_CACHE"] = str(tmp_path / "head-cache")
    env["GOH_NO_NATIVE"] = "1"  # the Python tier: no bin/goh rebuild in a scratch clone
    env.update(extra)
    return env


def _structural(gates: Path, repo: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(gates / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_a_consumer_runs_heads_gates_not_the_working_tree(
    dirty: Path, consumer: Path, tmp_path
) -> None:
    r = _structural(dirty, consumer, _env(tmp_path))
    assert PLANT not in r.stderr, "an uncommitted gate edit reached a consumer's gate"
    head = subprocess.run(
        ["git", "-C", str(dirty), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert (tmp_path / "head-cache" / head / "gates" / "structural.sh").is_file()


def test_goh_live_runs_the_working_tree_on_purpose(dirty: Path, consumer: Path, tmp_path) -> None:
    r = _structural(dirty, consumer, _env(tmp_path, GOH_LIVE="1"))
    assert PLANT in r.stderr, r.stderr


def test_a_sourced_lib_is_read_from_head_too(dirty: Path, consumer: Path, tmp_path) -> None:
    """ZoneWM's tools/gate.sh sources _common.sh itself; that path must not reach the tree either."""
    script = f". '{dirty}/gates/_common.sh'\ngoh_init consumer\ngoh_done\n"
    r = subprocess.run(
        ["bash", "-c", script],
        cwd=consumer,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert PLANT not in r.stderr, r.stderr


def test_python_imports_resolve_into_the_export(gates: Path, consumer: Path, tmp_path) -> None:
    """~/.zshrc exports PYTHONPATH at the live checkout; a checker started from the export must
    import tui.lib from the export, or the Python half is still live."""
    probe = gates / "gates" / "zz_probe.sh"
    probe.write_text(
        '#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/_from_head.sh"\n'
        'goh_from_head "${BASH_SOURCE[0]}" "$@"\n'
        'python3 -c "import tui.lib, sys; print(tui.lib.__file__)"\n'
    )
    subprocess.run(["git", "-C", str(gates), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(gates),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "probe",
        ],
        check=True,
    )
    try:
        r = subprocess.run(
            ["bash", str(probe)],
            cwd=consumer,
            capture_output=True,
            text=True,
            env=_env(tmp_path, PYTHONPATH=str(gates)),
            timeout=60,
            check=False,
        )
        assert str(tmp_path / "head-cache") in r.stdout, r.stdout + r.stderr
    finally:
        subprocess.run(["git", "-C", str(gates), "reset", "-q", "--hard", "HEAD~1"], check=True)


def test_a_test_that_drops_goh_live_is_refused_not_silently_run_on_head(
    gates: Path, consumer: Path, tmp_path
) -> None:
    env = _env(tmp_path, PYTEST_CURRENT_TEST="tests/x.py::t (call)")
    r = _structural(gates, consumer, env)
    assert r.returncode != 0
    assert "GOH_LIVE" in r.stderr, r.stderr


def test_the_export_is_built_once_per_commit(gates: Path, consumer: Path, tmp_path) -> None:
    env = _env(tmp_path)
    _structural(gates, consumer, env)
    cache = tmp_path / "head-cache"
    exports = [p for p in cache.iterdir() if not p.name.startswith(".")]
    stamp = exports[0].stat().st_mtime_ns
    _structural(gates, consumer, env)
    assert [p for p in cache.iterdir() if not p.name.startswith(".")] == exports
    assert exports[0].stat().st_mtime_ns == stamp, "an export was rebuilt for the same commit"


def test_the_self_hosted_gate_judges_its_own_tree_never_goh_dir() -> None:
    """THIS repo is the gates. Its push runs in a worktree of the pushed commit, where GOH_DIR
    names the shared checkout's HEAD export (C4) -- before C4, the shared working tree. Either way
    a step spelled "$GOH_DIR/tools/pytest.sh" ran SOMEONE ELSE's tests: the v0.22.0 push ran the
    export's, from a directory that is not a git repo, 19 failed + 26 errors. Its own gate names
    its own tree."""
    root = Path(__file__).resolve().parent.parent
    for rel, banned in ((".gatesrc", ("$GOH_DIR/", "$GOH/")), ("tools/gate.sh", ("GOH_DIR",))):
        live = [
            line
            for line in (root / rel).read_text().splitlines()
            if not line.lstrip().startswith("#") and any(b in line for b in banned)
        ]
        assert not live, f"{rel} runs gate code from GOH_DIR, not this tree: {live}"
