"""tests/test_proven.py's gates copy judges the native binary the same way whatever tag HEAD has.

GOH.PROVENTAGHEAD: tests/test_proven.py went red on every release commit (goh's CI on v0.126.0) and
green off one. Its `goh` fixture is the gates committed afresh, with no release tag reachable
(`unknown`), and its binary is the native build of THIS checkout, which reports `X.Y.Z` on a HEAD
carrying a release tag and `X.Y.Z-N-g<sha>` past one. structural.sh's currency check read a
release build as BEHIND `unknown`, so the suite's verdict was hidden state: whether the checkout
that built the binary had a tag on HEAD.
"""

from __future__ import annotations

import subprocess

import pytest
from test_proven import _git, env, goh, repo  # noqa: F401  (fixtures)
from timing_bounds import patience


@pytest.mark.parametrize(
    "built_at", ["9.9.9", "9.9.9-3-g0123456789", "unknown"],
    ids=["a-release-tagged-head", "past-a-release", "no-release"],
)  # fmt: skip
def test_any_native_build_serves_the_untagged_copy(goh, repo, env, tmp_path, built_at):  # noqa: F811
    """GOH.PROVENTAGHEAD: the native binary is built from THIS checkout, so it says this checkout's
    version -- `X.Y.Z` when HEAD carries a release tag (goh's CI, on every release commit), else
    `X.Y.Z-N-g<sha>`. The fixture is the gates committed afresh, no tag reachable (`unknown`), and
    structural.sh's currency check read a release build as BEHIND `unknown`: this suite went red on
    a tagged checkout only. With no release reachable there is no release to be behind or ahead of.
    Each build is the real binary under a `--version` that says where it was built."""
    shim = tmp_path / "built-at" / "goh"
    shim.parent.mkdir()
    shim.write_text(
        f'#!/bin/bash\n[ "$1" = --version ] && {{ echo "goh {built_at}"; exit 0; }}\n'
        f'exec "{env["GOH_BIN"]}" "$@"\n'
    )
    shim.chmod(0o755)
    (repo / "src.txt").write_text("staged\n")
    _git(repo, "add", "src.txt")
    got = subprocess.run(
        ["bash", str(goh / "gates" / "structural.sh"), "--staged"],
        cwd=repo, env=dict(env, GOH_BIN=str(shim)), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False, timeout=patience(120),
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    assert "goh binary serving this gate" not in got.stdout + got.stderr
