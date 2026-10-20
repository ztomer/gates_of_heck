"""`goh unreaped-spawn` is a port, so the measured table must read the same through both (Phase N1).

The table in `checks/_unreaped_spawn_table*.py` IS the specification (R2), and `--probe` runs it
through the Python checker only. Here every row goes through both, and the WHOLE verdict list is
compared -- line, tag and reason -- not just finding-or-clean, so a port that reaches the right
answer for the wrong reason shows. The whole-tree report on this repository, the largest real
corpus the suite owns, is compared byte for byte at both scopes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, commit_all, write

from reference_kit import load_reference, reference_path  # noqa: E402

from unreaped_spawn_table import PYTHON, PYTHON_EXTRA, RUST, SHELL  # noqa: E402
from unreaped_spawn_table_regressions import (  # noqa: E402
    PYTHON_RAW_QUOTE_CALIBRATION,
    PYTHON_REGRESSIONS,
    RUST_REGRESSIONS,
)

verdicts = load_reference("check_no_unreaped_spawn").verdicts

ROWS = (
    [(".rs", label, src) for label, src, _ in RUST + RUST_REGRESSIONS]
    + [
        (".py", label, src)
        for label, src, _ in PYTHON
        + PYTHON_EXTRA
        + PYTHON_REGRESSIONS
        + [PYTHON_RAW_QUOTE_CALIBRATION]
    ]
    + [(".sh", label, src) for label, src, _ in SHELL]
)


def native(goh: Path, source: str, ext: str) -> list[list]:
    r = subprocess.run(
        [str(goh), "unreaped-spawn", "--verdicts", ext],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize(
    ("ext", "label", "source"), ROWS, ids=[f"{e}:{l[:60]}" for e, l, _ in ROWS]
)
def test_every_table_row_reads_the_same_through_the_port(
    goh: Path, ext: str, label: str, source: str
) -> None:
    want = [list(v) for v in verdicts(source, ext)]
    assert native(goh, source, ext) == want, label


def test_the_table_is_not_empty() -> None:
    """A parity table over zero rows agrees with everything."""
    assert len(ROWS) >= 55, len(ROWS)


def _report(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR")}
    env["NO_COLOR"] = "1"
    r = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return r.returncode, r.stdout + r.stderr


def test_this_repositorys_full_report_is_byte_identical(goh: Path) -> None:
    """The whole-tree report over this repository, the largest real corpus the suite owns, read
    through the frozen reference and through the port, byte for byte."""
    py = _report([sys.executable, str(reference_path("checks/check_no_unreaped_spawn.py"))])
    rs = _report([str(goh), "unreaped-spawn"])
    assert rs == py


def test_the_staged_scope_is_the_tree_the_commit_produces(goh: Path, repo: Path) -> None:
    """`--staged` reads the INDEX, and the index is the TREE the commit produces -- not the diff
    the commit carries.

    The reference read the diff (`git diff --cached --name-only`) at `--staged`, so its staged
    report and the port's staged report cannot be compared over a tree: the port's scope is the
    index's whole test scope, because a guard is a CROSS-FILE contract and a diff cannot show it
    (`crates/goh/src/unreaped/scope.rs`). What CAN be compared, and is the property that matters,
    is the port's staged scope against the reference's FULL scope over the same tree: a clean
    checkout's index is its committed tree, so the two must read the same tree and say the same
    thing about it.

    Red before the change: the port's staged scope was the diff, and the diff of a clean checkout
    is empty -- "not applicable" against a tree of three test files.
    """
    write(
        repo,
        "tests/common/mod.rs",
        "use std::process::Child;\n\npub struct ReapOnDrop(pub Child);\n\n"
        "impl Drop for ReapOnDrop {\n    fn drop(&mut self) {\n"
        "        let _ = self.0.kill();\n        let _ = self.0.wait();\n    }\n}\n",
    )
    write(
        repo,
        "tests/test_uses.rs",
        "mod common;\n\nuse std::process::Command;\n\nuse common::ReapOnDrop;\n\n"
        "#[test]\nfn t() {\n"
        '    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));\n'
        "    assert!(child.0.id() > 0);\n}\n",
    )
    write(
        repo,
        "tests/test_guarded.py",
        "import subprocess\n\n\ndef test_x():\n    with subprocess.Popen(['x']) as proc:\n"
        "        assert proc is not None\n",
    )
    commit_all(repo)

    py = _report([sys.executable, str(reference_path("checks/check_no_unreaped_spawn.py"))], repo)
    rs = _report([str(goh), "unreaped-spawn", "--staged"], repo)
    assert rs == py
