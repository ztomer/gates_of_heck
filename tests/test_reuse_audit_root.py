"""GOH.ENABLEROOT: `reuse_audit.py enable` reads its fix from a git checkout even when the from-head
re-run (gates/_from_head.py) runs it from an export, a `git archive` with no history.

Split from test_reuse_audit.py (file-length headroom); it shares that file's store fixture.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT
from source_closure import copy_closure
from test_reuse_audit import FIELDS, fake_loopctl, store  # noqa: F401  (fixtures)

sys.path.insert(0, str(REPO_ROOT / "lib"))
import reuse_audit


def _fix_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fix"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_k.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('n', [1, 2])\n"
        "def test_the_new_input_misses(n):\n    pass\n"
    )
    fast_init(repo)
    for args in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    return repo


def _export_copy(tmp_path: Path) -> Path:
    """What gates/_from_head.py runs of an immutable export, `.goh-head` and no `.git`."""
    export = tmp_path / "head" / "abc123"
    export.mkdir(parents=True)
    (export / ".goh-head").write_text("abc123\n")
    schema = [str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "lib").glob("goh_*schema/*.sql")]
    copy_closure(export, "lib/reuse_audit.py", extra=("lib/lock_order.tsv", *schema))
    return export


def _enable_from(export: Path, cwd: Path, *argv: str, env: dict | None = None):
    run_env = {k: v for k, v in os.environ.items() if k not in ("GOH_DIR", "GOH_LIVE_ROOT")}
    run_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(export / "lib" / "reuse_audit.py"), "enable", "landed", *argv],
        cwd=cwd, env=run_env, capture_output=True, text=True, check=False,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


@pytest.mark.usefixtures("fake_loopctl")
def test_enable_from_a_non_git_export_finds_the_callers_checkout(tmp_path) -> None:
    """GOH.ENABLEROOT: the from-head re-run runs lib/reuse_audit.py from ~/.cache/goh/head/<sha>,
    not a git repository, so `--root`'s own-parent default refused every valid --fix ("not a test").
    The root is the checkout the export was cut from (GOH_LIVE_ROOT), else the caller's."""
    reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "red"))
    repo, export = _fix_repo(tmp_path), _export_copy(tmp_path)
    test = "tests/test_k.py::test_the_new_input_misses[1]"  # a parametrized id: its base name
    got = _enable_from(export, repo, "--fix", "HEAD", "--test", test)  # the cwd's toplevel
    assert got.returncode == 0, got.stderr
    assert reuse_audit.state("landed")["state"] == "on"
    reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "red"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    got = _enable_from(export, elsewhere, "--fix", "HEAD", "--test", test,
                       env={"GOH_LIVE_ROOT": str(repo)})  # fmt: skip
    assert got.returncode == 0, got.stderr  # the export's live root, from any cwd
    assert reuse_audit.state("landed")["state"] == "on"


@pytest.mark.usefixtures("fake_loopctl")
def test_enable_names_a_root_that_is_not_a_repository(tmp_path) -> None:
    reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "red"))
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(ValueError, match="is not a git repository") as err:
        reuse_audit.enable("landed", root=bare, fix="HEAD", test="tests/test_k.py::test_x")
    assert str(bare) in str(err.value) and "not a test" not in str(err.value)
    got = _enable_from(_export_copy(tmp_path), bare, "--fix", "HEAD", "--test", "t.py::test_x")
    assert got.returncode == 1 and "is not a git repository" in got.stderr, got.stderr
    assert "not a test" not in got.stderr
