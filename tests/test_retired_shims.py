"""The retired Python checkers' entry points: each kept one forwards to its native check, and no
other retired checker is left behind (Phase N3).

A shim exists because a consumer calls the file BY PATH; it must behave as `goh.sh <check>` does,
arguments and exit code included, or the consumer's gate silently changed. One that drifted into
running something else -- or a second copy of a checker kept "for now" -- is what this refuses.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "checks"))
from _retired import NATIVE, SHIMS  # noqa: E402

PAIRS = [(stem, NATIVE[stem]) for stem in SHIMS]


@pytest.mark.parametrize(("stem", "check"), PAIRS)
def test_a_shim_runs_its_native_check_with_the_arguments_given(stem, check, tmp_path) -> None:
    fake = tmp_path / "goh"
    fake.write_text('#!/bin/sh\necho "native $*"\nexit 3\n')
    fake.chmod(0o755)
    r = subprocess.run(
        [sys.executable, str(ROOT / "checks" / f"{stem}.py"), "--staged", "x y"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "GOH_BIN": str(fake), "GOH_LIVE": "1"},
        timeout=60,
    )
    assert r.stdout.strip() == f"native {check} --staged x y", r.stdout + r.stderr
    assert r.returncode == 3, "the native exit code is the shim's exit code"


def test_every_shim_is_listed_and_every_listed_shim_is_a_shim() -> None:
    forwarding = {
        p.stem
        for p in (ROOT / "checks").glob("check_*.py")
        if re.search(r'forward\("[\w-]+", __name__\)', p.read_text(encoding="utf-8"))
    }
    assert forwarding == {stem for stem, _ in PAIRS}
    for stem, check in PAIRS:
        text = (ROOT / "checks" / f"{stem}.py").read_text(encoding="utf-8")
        assert f'forward("{check}", __name__)' in text, stem
        assert len(text.splitlines()) < 15, f"{stem}.py is more than a forwarder"


@pytest.mark.parametrize(("stem", "check"), PAIRS)
def test_importing_a_shim_is_a_clear_error_never_an_exec(stem, check) -> None:
    """An exec at import time replaced the IMPORTING process: a pytest worker that imported one
    vanished with no output. Imported, a shim refuses by name instead."""
    r = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, 'checks'); import {stem}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 1 and f"run `goh {check}`" in r.stderr, r.stdout + r.stderr


def test_every_retired_checker_names_a_check_goh_dispatches() -> None:
    text = (ROOT / "gates" / "goh.sh").read_text(encoding="utf-8")
    table = text[text.index('case "$check" in') : text.index("esac")].split(")")[0]
    dispatched = set(re.findall(r"[a-z][a-z-]+", table.split("in", 1)[1]))
    assert set(NATIVE.values()) <= dispatched, sorted(set(NATIVE.values()) - dispatched)
    for stem in NATIVE:
        assert not (ROOT / "checks" / f"{stem}.py").exists() or stem in SHIMS, stem


def test_every_retired_entry_point_still_answers_by_path() -> None:
    """THE CLASS: a by-path entry point retired with no forwarder. The first cut kept only the
    forwarders an estate sweep measured, and missed two a consumer's gate called (ztools)."""
    missing = [
        stem
        for stem in NATIVE
        if not (ROOT / "checks" / f"{stem}.py").exists()
        and not (ROOT / "checks" / f"{stem}.sh").exists()
    ]
    assert not missing, missing


def test_the_shell_lint_forwarder_runs_its_native_check(tmp_path) -> None:
    fake = tmp_path / "goh"
    fake.write_text('#!/bin/sh\necho "native $*"\nexit 3\n')
    fake.chmod(0o755)
    r = subprocess.run(
        ["bash", str(ROOT / "checks" / "check_shell_lint.sh"), "--staged"],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", "GOH_BIN": str(fake), "GOH_LIVE": "1"},
    )  # fmt: skip
    assert r.stdout.strip() == "native shell-lint --staged", r.stdout + r.stderr
    assert r.returncode == 3
