"""A gate step's tool is in a table, or the step is refused (GOH.REQTOOLS, GOH.CLASS.PARITY).

gates/required_tools.py derives a repo's tools from the tables it knows, so a step running a tool no
table names was installed by nobody on a hosted runner: ztools' `cargo deny` step stayed green only
through a hand list in its own workflow. gates/step_tools.py refuses such a step from 2026-11-23
(crate::grace's day, read from grace.rs), in `required_tools.py --repo` and in local_ci.sh alike.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "gates"))
import required_tools
import step_tools

CLI = REPO_ROOT / "gates" / "required_tools.py"
LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"
MANIFEST = {r["tool"] for r in required_tools.rows()}
# ztools' .gatesrc step (2026-11-15), the case that motivated the refusal.
DENY = "cargo deny --manifest-path rust/Cargo.toml check -D warnings"


def _repo(tmp_path: Path, steps: str, extra: str = "") -> Path:
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    write_gatesrc(d, f"GOH_CI_STEPS='{steps}'\n{extra}")
    return d


def _cli(repo: Path, *args: str, grace: str) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_GRACE=grace, GOH_LIVE="1")
    return subprocess.run(
        [sys.executable, str(CLI), "--repo", str(repo), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_ztools_cargo_deny_is_refused_once_the_grace_is_over(tmp_path):
    repo = _repo(tmp_path, f"cargo test:{DENY}")
    assert step_tools.unnamed(str(repo), MANIFEST) == [f"cargo-deny (step: {DENY})"]
    for mode in (["--unnamed"], ["--install"], ["--names"]):
        r = _cli(repo, *mode, grace="over")
        assert r.returncode == 1, (mode, r.stdout, r.stderr)
        assert "refused: cargo-deny" in r.stderr and "GOH_STEP_TOOLS" in r.stderr, r.stderr
    r = _cli(repo, "--install", grace="within")
    assert r.returncode == 0, r.stderr
    assert "refused from 2026-11-23: cargo-deny" in r.stderr, r.stderr


def test_a_tool_the_repo_declares_or_a_table_names_passes(tmp_path):
    repo = _repo(tmp_path, f"cargo test:{DENY}", "GOH_STEP_TOOLS='cargo-deny swiftformat'\n")
    assert step_tools.unnamed(str(repo), MANIFEST) == []
    named = [
        '"$GOH_DIR/gates/rust_gate.sh" .',
        "python3 tools/check_x.py",
        "python3 -m pytest tools/tests -q",
        "cargo audit --file rust/Cargo.lock",  # the manifest's cargo-audit
        "[menubar,produces=bin/x] make -C tui/menubar build",
        "FOO=1 env cargo build --locked",
        "ruff check tools/",
        "./scripts/lint.sh",
        "$GOH_PY tools/x.py",
        "cargo clippy -- -D warnings",
    ]
    steps = ":".join(named)  # one GOH_CI_STEPS value
    assert step_tools.unnamed(str(_repo(tmp_path, steps)), MANIFEST) == []


def test_an_unnamed_leading_tool_and_module_are_found(tmp_path):
    repo = _repo(tmp_path, "swiftformat --lint .:python3 -m mypy src:time detekt --all")
    assert [line.split(" ")[0] for line in step_tools.unnamed(str(repo), MANIFEST)] == [
        "detekt",
        "mypy",
        "swiftformat",
    ]


def test_the_python_grace_is_the_rust_one():
    text = (REPO_ROOT / "crates/goh/src/grace.rs").read_text(encoding="utf-8")
    over_from = int(re.search(r"OVER_FROM: u64 = ([\d_]+);", text).group(1).replace("_", ""))
    env = os.environ.pop("GOH_GRACE", None)
    try:
        assert not step_tools.grace_over(over_from - 1)
        assert step_tools.grace_over(over_from)
    finally:
        if env is not None:
            os.environ["GOH_GRACE"] = env


@pytest.mark.parametrize(("grace", "code"), [("over", 1), ("within", 0)])
def test_local_ci_refuses_the_same_step_before_running_any(tmp_path, grace, code):
    marker = tmp_path / "ran"
    repo = _repo(tmp_path, f"touch {marker}:frobnicate --all")
    env = hermetic_env(GOH_GRACE=grace)
    r = subprocess.run(
        ["/bin/bash", str(LOCAL_CI)], cwd=repo, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    assert "frobnicate" in r.stderr, r.stderr
    if code:
        assert r.returncode == 1 and not marker.exists(), r.stdout + r.stderr
    else:
        assert marker.exists(), "within the grace the steps run"
