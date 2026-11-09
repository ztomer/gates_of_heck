"""A test suite a gate launches never inherits git's repository variables (GOH.HOOKGITENV).

THE CLASS. git hands a hook GIT_DIR, GIT_INDEX_FILE and the rest of what `git rev-parse
--local-env-vars` prints. The stock hook drops them at entry (`goh_hook_unbind`), but the staged
scope needs the index being committed, so a gate that is documented to `goh_bind_hook_index` in its
own shell puts GIT_INDEX_FILE back -- and the suite the gate launches next inherits it. Every `git`
that suite runs in its own temp fixture then writes the COMMIT's index (.loop, 2026-11-08: 7 tests
red only when committed). The gate's own git keeps the binding; what a gate LAUNCHES as a test does
not: `goh_step` (pytest, cargo test, swift test, xcodebuild test), `local_ci.sh` steps and the
coverage gate's test runs all start the suite with `goh_unbind_git` applied.

Each test commits for real, through the stock hook, in a linked worktree (the shape whose
variables are absolute); `-a` and `<paths>` are the commits whose index is a TEMPORARY file.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_commit_hook_git_env import _commit, _consumer, _git

# The suite a consumer gate launches: it records which of git's local variables it was handed.
PROBE = """\
import json
import os
import subprocess
from pathlib import Path

NAMES = subprocess.run(
    ["git", "rev-parse", "--local-env-vars"],
    capture_output=True,
    text=True,
    check=True,
    stdin=subprocess.DEVNULL,
).stdout.split()


def test_probe():
    seen = {k: os.environ[k] for k in NAMES if k in os.environ}
    Path(os.environ["PROBE_OUT"]).write_text(json.dumps(seen))
"""

# A consumer gate that reads the index being committed with its OWN git, bound in its own shell
# (the documented use of goh_bind_hook_index), and then launches its suite through the house step.
GATE = """#!/usr/bin/env bash
set -euo pipefail
. "$GOH_DIR/gates/_git_env.sh"
goh_bind_hook_index
git diff --cached --name-only > "$STAGED_OUT"
exec "$GOH_DIR/gates/py_gate.sh" .
"""


def _suite_commit(tmp_path: Path, how: str):
    main, wt = _consumer(
        tmp_path,
        GATE,
        (REPO_ROOT / "hooks" / "pre-commit").read_bytes(),
        {"test_probe.py": PROBE, ".gatesrc": "GOH_MAX_LINES=500\n"},
    )
    out = tmp_path / "out"
    out.mkdir()
    (wt / "test_probe.py").write_text(PROBE + "\n\ndef test_two():\n    pass\n")
    extra: tuple[str, ...] = ()
    if how == "plain":
        _git(wt, "add", "test_probe.py")
    else:
        extra = ("-a",) if how == "all" else ("test_probe.py",)  # modified, NOT staged
    env = hermetic_env(PROBE_OUT=str(out / "seen.json"), STAGED_OUT=str(out / "staged.txt"))
    got = _commit(wt, env, *extra)
    return main, got, out


@pytest.mark.parametrize("how", ["plain", "all", "paths"])
def test_a_suite_the_commit_gate_runs_sees_none_of_gits_local_variables(tmp_path, how):
    main, got, out = _suite_commit(tmp_path, how)

    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    seen = json.loads((out / "seen.json").read_text())
    assert seen == {}, f"the suite inherited git's repository variables: {seen}\n{text}"
    assert _git(main, "log", "--format=%s", "wt").splitlines() == ["ours", "one"], text


@pytest.mark.parametrize("how", ["all", "paths"])
def test_the_gates_own_git_still_reads_the_index_being_committed(tmp_path, how):
    """The converse: scrubbing the suite's environment must not cost the gate its own binding.
    The edit is modified and NOT staged; only the commit's temporary index lists it."""
    _main, got, out = _suite_commit(tmp_path, how)

    assert got.returncode == 0, got.stdout + got.stderr
    assert (out / "staged.txt").read_text().split() == ["test_probe.py"]


# ── the other launchers: local_ci.sh steps and the coverage gate's test runs ──

BOUND = {
    "GIT_INDEX_FILE": "/nonexistent/index.lock",
    "GIT_DIR": "/nonexistent/.git",
    "GIT_WORK_TREE": "/nonexistent",
}


def _dump_env(path: Path) -> str:
    return f"env > {path}"


def _names_in(path: Path) -> list[str]:
    return [line.split("=", 1)[0] for line in path.read_text().splitlines() if "=" in line]


def test_a_local_ci_step_sees_none_of_gits_local_variables(tmp_path):
    proj = tmp_path / "proj"
    fast_init(proj)
    out = tmp_path / "env.txt"
    (proj / ".gatesrc").write_text(f"GOH_CI_STEPS='{_dump_env(out)}'\n")
    got = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh")],
        cwd=proj,
        env=hermetic_env(**BOUND),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert got.returncode == 0, got.stdout + got.stderr
    assert sorted(set(_names_in(out)) & set(BOUND)) == []


def test_the_coverage_gates_python_run_sees_none_of_gits_local_variables(tmp_path):
    proj = tmp_path / "proj"
    fast_init(proj)
    out = tmp_path / "env.txt"
    (proj / "test_probe.py").write_text(
        "import os\nfrom pathlib import Path\n\n\ndef test_probe():\n"
        '    Path(os.environ["PROBE_OUT"]).write_text("\\n".join(f"{k}=" for k in os.environ))\n'
    )
    got = subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "gates" / "coverage_gate.sh"),
            "--lang",
            "py",
            "--floor",
            "0",
        ],
        cwd=proj,
        env=hermetic_env(PROBE_OUT=str(out), **BOUND),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert out.exists(), got.stdout + got.stderr
    assert sorted(set(_names_in(out)) & set(BOUND)) == []


def test_the_rust_coverage_runs_unbind_before_cargo_test():
    """cargo-llvm-cov is too slow for a unit suite (test_coverage_gate.py); the two subshells that
    start the suite are read instead: each unbinds before its first launch."""
    body = (REPO_ROOT / "gates" / "_coverage_rust.sh").read_text()
    for launch in ("cargo test --locked --workspace", 'bash -c "$EXTERNAL"'):
        before = body[: body.index(launch)]
        assert re.search(r"^\s*goh_unbind_git\b", before.rsplit("if (", 1)[-1], re.MULTILINE), (
            launch
        )
