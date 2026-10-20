"""The proven cache, and the ARTIFACTS a step declares (divoom-control, 2026-10-20).

THE CLASS. The cache remembered "this step exited 0" and read that as "the artifact a later step
needs is there". The pre-push gate runs in a FRESH worktree: the build step had been proven on the
same commit by an earlier push attempt, so it was SKIPPED, so it left no binary behind, and the
step that consumes it failed with "binary not found" -- a reason that had nothing to do with the
tree. Measured in divoom-control: step 27 skipped as "proven on this tree 8m ago", step 30 failed.

THE FIX is at the class, not at divoom's step list: a step declares the artifacts it produces and
consumes, and the cache honours the declaration.

    [produces=target/debug/divoomd] cargo build -p divoomd --locked
    [consumes=target/debug/divoomd] python3 tools/check_built_binaries.py

A record may only be written when every declared artifact is THERE (a build that "succeeded" and
produced nothing is not a proof of anything), and a hit is only a hit while every declared artifact
is still there, UNCHANGED.

THE THREE WAYS IT CAN LIE, each with its own test:

  1. a build that "succeeded" but produced no artifact -> the step is RED, and nothing is recorded;
  2. a stamp that matches a STALE artifact -> the artifact's BYTES changed -> the hit is void;
  3. a step that consumes a build from another commit -> the consumed artifact's bytes changed ->
     the consumer's proof is void.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from test_proven import _git, env, goh, proven, records, repo, runs  # noqa: F401

BUILD = (
    '[produces=build/app.bin] echo ran >> "$COUNT"; mkdir -p build; printf built > build/app.bin'
)
CONSUME = "[consumes=build/app.bin] test -f build/app.bin"


def _local_ci(goh_dir: Path, repo_dir: Path, run_env: dict, *steps: str):
    argv = ["bash", str(goh_dir / "gates" / "local_ci.sh"), "--steps-only"]
    for step in steps:
        argv += ["--step", step]
    argv.append(".")
    return subprocess.run(
        argv,
        cwd=repo_dir,
        env=run_env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )


# ── the class, reproduced ───────────────────────────────────────────────────────────────────────


def test_a_build_whose_artifact_is_gone_is_not_skipped(goh, repo, env):
    """THE CLASS, at the hook: the artifact is gone (the push gate's fresh worktree), so the step
    that would produce it must run again -- a cache that says "proven" over a tree with no binary
    is a cache that skipped the build."""
    assert proven(goh, repo, env, BUILD).returncode == 0
    assert (repo / "build" / "app.bin").is_file()
    (repo / "build" / "app.bin").unlink()
    again = proven(goh, repo, env, BUILD)
    assert again.returncode == 0, again.stdout + again.stderr
    assert (repo / "build" / "app.bin").is_file(), "the step was skipped and left no artifact"
    assert runs(env) == 2, again.stdout + again.stderr


def test_the_push_gate_re_runs_the_build_so_the_consumer_does_not_fail(goh, repo, env):
    """THE CLASS, at the push: two steps, the second consuming what the first produces. Without
    the declaration the build is skipped (it was proven on this tree), the artifact is not there,
    and the consumer fails for a reason that has nothing to do with the tree -- exactly what
    divoom-control measured."""
    first = _local_ci(goh, repo, env, BUILD, CONSUME)
    assert first.returncode == 0, first.stdout + first.stderr
    assert runs(env) == 1, first.stdout + first.stderr
    (repo / "build" / "app.bin").unlink()  # the fresh worktree: the binary is not there
    second = _local_ci(goh, repo, env, BUILD, CONSUME)
    assert second.returncode == 0, second.stdout + second.stderr
    assert runs(env) == 2, "the build was skipped, so the consumer had no artifact"
    assert (repo / "build" / "app.bin").is_file()


# ── the three ways it can lie ────────────────────────────────────────────────────────────────────


def test_a_build_that_succeeded_and_produced_nothing_is_red(goh, repo, env):
    """LIE 1: the step exits 0 and the artifact is not there. That is not a proof of anything,
    and the cache must not remember it -- or the NEXT run skips it and leaves the same hole."""
    step = '[produces=build/app.bin] echo "the build says it worked"'
    r = proven(goh, repo, env, step)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "build/app.bin" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert "not there" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert records(repo) == [], "a step that produced nothing was remembered"


def test_a_stale_artifact_does_not_satisfy_a_hit(goh, repo, env):
    """LIE 2: the artifact is THERE, and it is not the one the proof was about (a stamp that
    matches a stale build). The bytes are what the proof names, so the hit is void."""
    assert proven(goh, repo, env, BUILD).returncode == 0
    (repo / "build" / "app.bin").write_text("stale\n")
    again = proven(goh, repo, env, BUILD)
    assert again.returncode == 0, again.stdout + again.stderr
    assert runs(env) == 2, "a stale artifact satisfied the cache"
    assert (repo / "build" / "app.bin").read_text() == "built"


def test_a_consumer_whose_artifact_is_another_builds_is_not_skipped(goh, repo, env):
    """LIE 3: the step CONSUMES a build, and the build it consumed is not the one on disk (another
    commit's, or a rebuild). Its proof was about those bytes, so it must run again."""
    (repo / "build").mkdir(exist_ok=True)
    (repo / "build" / "app.bin").write_text("built\n")
    step = '[consumes=build/app.bin] echo consumed >> "$COUNT"'
    assert proven(goh, repo, env, step).returncode == 0
    (repo / "build" / "app.bin").write_text("another commit's build\n")
    again = proven(goh, repo, env, step)
    assert again.returncode == 0, again.stdout + again.stderr
    assert runs(env) == 2, "the consumer was skipped over a different artifact"


def test_a_consumer_without_its_artifact_is_red(goh, repo, env):
    """A step that says it consumes something, and the something is not there, proved nothing."""
    step = '[consumes=build/app.bin] echo consumed >> "$COUNT"'
    r = proven(goh, repo, env, step)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "build/app.bin" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert "not there" in (r.stdout + r.stderr), r.stdout + r.stderr
    assert records(repo) == []


# ── the declaration itself ───────────────────────────────────────────────────────────────────────


def test_a_declaration_is_part_of_the_key(goh, repo, env):
    """A record written before the declaration existed can never be found: the step string is the
    key, and the declaration is in it. Two steps whose COMMAND is the same and whose declaration
    differs are two different proofs."""
    produces = '[produces=build/app.bin] echo ran >> "$COUNT"; mkdir -p build; printf built > build/app.bin'
    consumes = '[consumes=build/app.bin] echo ran >> "$COUNT"; mkdir -p build; printf built > build/app.bin'
    assert proven(goh, repo, env, produces).returncode == 0
    assert runs(env) == 1
    assert proven(goh, repo, env, consumes).returncode == 0
    assert runs(env) == 2, "a record was found for a different step string"


def test_an_unknown_declaration_is_refused(goh, repo, env):
    """A typo in a declaration is a gate that silently checks nothing."""
    r = proven(goh, repo, env, "[produecs=build/app.bin] echo ran")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "produces" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_a_declaration_alongside_the_resource_tags_is_honoured(goh, repo, env):
    """The prefix carries two things: the tags (steps sharing one never overlap) and the
    declarations. A tag is not a declaration -- and a declaration beside a tag is still a
    declaration: the step that declares an artifact it does not produce is red."""
    step = '[db,screen,produces=build/app.bin] echo ran >> "$COUNT"'
    r = _local_ci(goh, repo, env, step)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "build/app.bin" in r.stdout + r.stderr, r.stdout + r.stderr
    assert "not a declaration" not in r.stdout + r.stderr, r.stdout + r.stderr
