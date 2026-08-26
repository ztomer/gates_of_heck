"""check_disk_hygiene.py — partial-failure honesty + per-root free space.

The class (2026-08-25): _du_bytes treated ANY nonzero du exit as "no data"
and returned 0 — so an unreadable subdirectory made a multi-GB scratch tree
report ZERO bytes and silently PASS its ceiling. A gate that reads cleaner
than reality is worse than no gate. Now: rc!=0 with parseable stdout uses
the PARTIAL measurement and warns with the stderr tail; only EMPTY stdout
yields zero. Free space is also checked per scratch-root volume, not just
on the cwd volume.
"""

import argparse
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT


def _load():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_disk_hygiene_testbed",
        REPO_ROOT / "checks" / "check_disk_hygiene.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


disk = _load()


@pytest.fixture(autouse=True)
def _relockable_tmp(tmp_path):
    """Restore read permission so pytest's own tmp cleanup can delete the
    chmod-000 trees these tests create."""
    yield
    for p in sorted(tmp_path.rglob("*"), reverse=True):
        try:
            if p.is_dir():
                p.chmod(0o755)
        except OSError:
            pass


def _unreadable_tree(tmp_path, payload=b"\0" * 2_000_000):
    """A tree whose 'locked' subtree cannot be read by du, plus a readable
    sibling that gives du something to measure."""
    tree = tmp_path / "scratch"
    locked = tree / "locked"
    locked.mkdir(parents=True)
    (locked / "secret.bin").write_bytes(b"x" * 1024)
    readable = tree / "readable"
    readable.mkdir()
    (readable / "bulk.bin").write_bytes(payload)
    os.chmod(locked, 0o000)
    return tree


def _run_main(monkeypatch, tree, *extra):
    monkeypatch.setattr(disk, "_scratch_roots", lambda: [tree])
    argv = ["check_disk_hygiene.py", "--min-free-gb", "0", *extra]
    monkeypatch.setattr(sys, "argv", argv)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = disk.main()
    return code, out.getvalue(), err.getvalue()


# ---- _du_bytes contract -------------------------------------------------------


def test_du_partial_failure_returns_parsed_value_with_warning(tmp_path):
    tree = _unreadable_tree(tmp_path)
    result = disk._du_bytes(tree)
    val, warn = result
    assert val > 0, "partial du failure collapsed to 0 bytes — the old lie"
    assert warn, "partial du failure must produce a named warning"
    assert "exited 1" in warn


def test_du_total_failure_returns_zero_with_warning(tmp_path):
    missing = tmp_path / "nope"
    val, warn = disk._du_bytes(missing)
    assert val == 0 and warn


# ---- end-to-end through main() -------------------------------------------------


def test_unreadable_subdir_still_measured_passes_ceiling_with_warning(
    tmp_path, monkeypatch
):
    tree = _unreadable_tree(tmp_path)
    code, out, err = _run_main(monkeypatch, tree)  # default 25GB ceiling
    assert code == 0, (out, err)
    assert "⚠" in err and "exited 1" in err, (
        "pass-with-warning must say WHY the measure was partial"
    )


def test_unreadable_tree_over_ceiling_fails(tmp_path, monkeypatch):
    tree = _unreadable_tree(tmp_path)
    code, out, err = _run_main(monkeypatch, tree,
                               "--max-scratch-gb", "0")
    assert code == 1, (
        "an unreadable tree must not masquerade as empty and pass: "
        f"{out} {err}"
    )
    assert "holds" in err


def test_free_space_checked_on_scratch_root_volume_too(tmp_path, monkeypatch):
    class FakeUsage:
        free = 1 * disk.GIB
        used = 10 * disk.GIB
        total = 11 * disk.GIB

    tree = tmp_path / "scratch"
    tree.mkdir()
    # Fake ONLY the scratch root's volume: the cwd-volume check sees real
    # disk, so a failure here can only come from the PER-ROOT check.
    real = disk.shutil.disk_usage

    def fake(p):
        return FakeUsage() if Path(p) == tree else real(p)

    monkeypatch.setattr(disk.shutil, "disk_usage", fake)
    code, out, err = _run_main(monkeypatch, tree, "--min-free-gb", "5")
    assert code == 1
    assert "free" in err
