"""A skipped step's products are owed to the steps that run (GOH.PROVENBUILD).

CadGoose2, 2026-11-11 (CG2.PUSHGATE): an earlier push of ddb14af proved `bash scripts/build.sh
Release` and failed later, so the next push's fresh export SKIPPED the build ("proven on this tree
4m ago by local_ci -- skipped"), had no build/ or libMLXBridge.a, and 10 of 54 later steps failed.
The cache knew a build only by its tool's name (`proven_undeclared_build`: cargo, swift, go, ...),
so any repo building through a script wrapper was exposed until it hand-declared `produces=`.

The class, not the heuristic. A step's real run is MEASURED (gates/_proven_made.sh): the ignored
entries present after it and not before are what it made, kept per step string beside the records.
A run that began with no ignored file (a push export) measures completely, and only such a
measurement says a step made NOTHING. A hit whose products are not here -- or, in such a fresh
tree, whose products were never measured completely -- is HOLLOW: skipped only when no later step
runs (a skipped step reads nothing, so a ship push of a tree proven whole still skips its builds),
re-run when one does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import test_proven
from test_proven import _git, runs
from test_proven_artifacts import _local_ci

# The fixtures, by assignment (an import would be shadowed by each argument).
goh, repo, env = test_proven.goh, test_proven.repo, test_proven.env

WRAPPER = 'echo ran >> "$COUNT"\nmkdir -p build/Release\nprintf built > build/Release/libX.a\n'
BUILD = "bash scripts/build.sh Release"
# The consumer fails on the first push for a reason outside the tree (FAIL_ONCE is no key input),
# as CadGoose2's did, so only the build is proven on the tree.
CONSUME = 'test -f build/Release/libX.a && test -z "$FAIL_ONCE"'


def _wrapped(checkout: Path) -> None:
    """The repo builds through a script, as CadGoose2's `bash scripts/build.sh Release` does."""
    (checkout / "scripts").mkdir()
    (checkout / "scripts" / "build.sh").write_text(WRAPPER)
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "wrapper")


def _export(checkout: Path, name: str) -> Path:
    """A fresh worktree of HEAD, as the push gate makes: the records are shared, build/ is not."""
    wt = checkout.parent / name
    _git(checkout, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    return wt


def _ci(goh: Path, where: Path, env: dict, *steps: str, **extra: str):
    r = _local_ci(goh, where, {**env, **extra}, *steps)
    return r, r.stdout + r.stderr


def test_a_wrapper_build_proven_by_a_failed_push_is_rerun_in_the_next_export(goh, repo, env):
    _wrapped(repo)
    first, out = _ci(goh, _export(repo, "push1"), env, BUILD, CONSUME, FAIL_ONCE="1")
    assert first.returncode == 1, out
    assert runs(env) == 1
    second, out = _ci(goh, _export(repo, "push2"), env, BUILD, CONSUME)
    assert second.returncode == 0, (
        "the wrapper build was skipped, so its consumer had no build/\n" + out
    )
    assert runs(env) == 2
    assert "build/" in out, "the re-run does not name the products it is owed for:\n" + out


def test_a_build_into_a_directory_an_earlier_step_made_is_its_product_too(goh, repo, env):
    """`cargo clippy` makes target/ first; the wrapper's release build only writes into it."""
    _wrapped(repo)
    first_step = "mkdir -p build/cache && touch build/cache/x"
    first, out = _ci(goh, _export(repo, "push1"), env, first_step, BUILD, CONSUME, FAIL_ONCE="1")
    assert first.returncode == 1, out
    second, out = _ci(goh, _export(repo, "push2"), env, first_step, BUILD, CONSUME)
    assert second.returncode == 0, "a build into an existing build/ was credited nothing\n" + out
    assert runs(env) == 2


def test_a_build_proven_in_a_warm_checkout_is_rerun_in_a_fresh_export(goh, repo, env):
    """The checkout had build/ before the gate ever ran, so that run could not see it made it."""
    _wrapped(repo)
    (repo / "build").mkdir()
    (repo / "build" / "old").write_text("a build from before\n")
    first, out = _ci(goh, repo, env, BUILD, CONSUME, FAIL_ONCE="1")
    assert first.returncode == 1, out
    second, out = _ci(goh, _export(repo, "push"), env, BUILD, CONSUME)
    assert second.returncode == 0, "a warm proof skipped the build in a fresh export\n" + out
    assert runs(env) == 2


def test_where_the_products_are_there_the_build_is_skipped(goh, repo, env):
    """The accept direction: the same tree with its build/ intact keeps the cache's whole point."""
    _wrapped(repo)
    first, out = _ci(goh, repo, env, BUILD, CONSUME, FAIL_ONCE="1")
    assert first.returncode == 1, out
    second, out = _ci(goh, repo, env, BUILD, CONSUME)
    assert second.returncode == 0, out
    assert runs(env) == 1, "a build whose products are present was not skipped\n" + out


def test_a_hollow_proof_with_no_later_step_running_is_still_skipped(goh, repo, env):
    """A tree proven whole (a ship push after its land): nothing runs to read the products."""
    _wrapped(repo)
    first, out = _ci(goh, _export(repo, "land"), env, BUILD, CONSUME)
    assert first.returncode == 0, out
    second, out = _ci(goh, _export(repo, "push"), env, BUILD, CONSUME)
    assert second.returncode == 0, out
    assert runs(env) == 1, "a build was re-run with no step left to need it\n" + out


def test_a_step_that_made_only_caches_or_nothing_hits_in_a_fresh_export(goh, repo, env):
    """A lint that left `__pycache__/` and `.pytest_cache/` made nothing a later step needs."""
    (repo / ".gitignore").write_text("build/\n__pycache__/\n.pytest_cache/\n")
    _git(repo, "commit", "-qam", "ignore caches")
    lint = ('echo ran >> "$COUNT"; mkdir -p pkg/__pycache__ .pytest_cache; '
            ": > pkg/__pycache__/m.pyc; : > .pytest_cache/v")  # fmt: skip
    later = 'test -z "$FAIL_ONCE"'
    first, out = _ci(goh, _export(repo, "push1"), env, lint, later, FAIL_ONCE="1")
    assert first.returncode == 1, out
    second, out = _ci(goh, _export(repo, "push2"), env, lint, later)
    assert second.returncode == 0, out
    assert runs(env) == 1, "a step that made only caches lost its proof in a fresh export\n" + out


def test_the_single_step_cli_runs_a_hollow_proof(goh, repo, env):
    """gates/proven.sh sees one step and no later ones, so a hollow hit there is never skipped."""
    _wrapped(repo)
    first, out = _ci(goh, _export(repo, "push1"), env, BUILD, CONSUME, FAIL_ONCE="1")
    assert first.returncode == 1, out
    wt = _export(repo, "push2")
    argv = ["bash", str(goh / "gates" / "proven.sh"), "--", BUILD]
    r = subprocess.run(argv, cwd=wt, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert runs(env) == 2, "proven.sh skipped a build whose products are not here\n" + r.stdout
