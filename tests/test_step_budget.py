"""A caller's step budget reaches the steps inside the step it bounds (GOH.COVPHASES, GOH.CI107).

The hosted workflow sets GOH_LCI_TIMEOUT=2700: local_ci.sh gives `./gates/rust_gate.sh` 2700 s.
But rust_gate.sh runs every check through `goh_step`, whose ceiling is GOH_STEP_TIMEOUT -- which it
unsets on entry, so a fixture's environment never decides a verdict -- and so falls back to the
fixed 1800 s. The coverage step was cut at 1800 s inside a step the caller had given 2700 s
(run 43331499075): the budget the caller set never reached the step that needed it.

Now local_ci.sh hands each step its own ceiling as GOH_STEP_BUDGET, and a `goh_step` (both tiers)
whose repo sets no GOH_STEP_TIMEOUT falls back to that budget before the 1800 s constant. The
repo's own file still wins: the budget is a default, never an override.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

CEILING = re.compile(r"\((≤\d+s|UNBOUNDED[^)]*)\)")


def test_local_ci_hands_each_step_its_own_ceiling(tmp_path: Path) -> None:
    seen = tmp_path / "seen"
    (tmp_path / ".gatesrc").write_text(
        f"GOH_CI_STEPS='echo \"${{GOH_STEP_BUDGET-unset}}\" >{seen}'\n"
    )
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_LCI_TIMEOUT="2700"),
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text().strip() == "2700", "the step never learned the ceiling it runs under"


def _goh_step(tmp_path: Path, gatesrc: str, **env: str) -> set[str]:
    (tmp_path / ".gatesrc").write_text(gatesrc)
    script = (
        f"cd '{tmp_path}'; unset GOH_STEP_TIMEOUT; [ -f .gatesrc ] && . ./.gatesrc; "
        f". '{REPO_ROOT}/gates/_common.sh'; goh_init outer; goh_step one true; goh_done"
    )
    r = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        env=hermetic_env(**env),
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return set(CEILING.findall(r.stdout + r.stderr))


def test_goh_step_falls_back_to_the_callers_budget(tmp_path: Path) -> None:
    assert _goh_step(tmp_path, "", GOH_STEP_BUDGET="2700") == {"≤2700s"}
    assert _goh_step(tmp_path, "") == {"≤1800s"}, "no budget, no file: the constant"
    assert _goh_step(tmp_path, "GOH_STEP_TIMEOUT=77\n", GOH_STEP_BUDGET="2700") == {"≤77s"}, (
        "the repo's own ceiling must beat a caller's default"
    )


def test_a_bad_budget_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / ".gatesrc").write_text("")
    r = subprocess.run(
        ["/bin/bash", "-c", f". '{REPO_ROOT}/gates/_common.sh'; goh_init outer; goh_step one true"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_STEP_BUDGET="27OO"),
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 2, r.stdout + r.stderr
    assert "GOH_STEP_BUDGET" in r.stderr


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_rust_gates_steps_run_under_the_callers_budget(tmp_path: Path, goh: Path) -> None:
    """The defect itself: rust_gate.sh's steps said `(≤1800s)` under a 2700 s caller."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "b"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
    )
    (repo / "src" / "lib.rs").write_text("pub fn f() -> u64 {\n    1\n}\n")
    (repo / ".gatesrc").write_text("GOH_DEPS_OFFLINE=1\n")
    (repo / ".gitignore").write_text("target/\n")
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"],
        cwd=repo,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    fast_init(repo)
    bin_dir, _ = _fake_cargo.install(tmp_path / "fake-cargo")
    env = hermetic_env(
        drop_git=True,
        GOH_BIN=str(goh),
        GOH_PROVEN="0",
        GOH_RUST_GROUPS="crate",
        GOH_STEP_BUDGET="2700",
    )
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "rust_gate.sh"), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert CEILING.findall(r.stdout + r.stderr), r.stdout + r.stderr
    assert set(CEILING.findall(r.stdout + r.stderr)) == {"≤2700s"}, r.stdout + r.stderr


def test_the_native_tier_falls_back_to_the_callers_budget(repo: Path, goh: Path) -> None:
    def ceilings(**env: str) -> set[str]:
        out = subprocess.run(
            [str(goh), "structural", "--full"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=hermetic_env(**env),
            timeout=120,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        return set(CEILING.findall(out.stdout))

    assert ceilings(GOH_STEP_BUDGET="2700") == {"≤2700s"}
    (repo / ".gatesrc").write_text("GOH_STEP_TIMEOUT=77\n")
    assert ceilings(GOH_STEP_BUDGET="2700") == {"≤77s"}


def test_the_budget_is_not_dropped_on_entry() -> None:
    """rust_gate.sh's scrub is for a FIXTURE's configuration; the budget is the caller's bound."""
    text = (REPO_ROOT / "gates" / "rust_gate.sh").read_text(encoding="utf-8")
    scrub = text[text.index("unset GOH_COV_FLOOR_RUST") :].split("2>/dev/null", 1)[0]
    assert "GOH_STEP_BUDGET" not in scrub
