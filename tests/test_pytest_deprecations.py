"""A pytest deprecation fails the suite (GOH.ASYNCIOSCOPE).

pytest-asyncio's "asyncio_default_fixture_loop_scope is unset" PytestDeprecationWarning printed in
every pre-step for weeks, because a warning is a line nobody reads. `filterwarnings` in
pyproject.toml makes any PytestDeprecationWarning an error, so the next one fails the land that
brings it. The planted run reads the SUITE'S OWN filter from pyproject.toml, so deleting it there
turns this red; the calibration is the same planted test under no filter, which must pass with the
warning, so a "red" can never be a planted test that fails for another reason.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

PLANTED = """\
import warnings

import pytest


def test_a_pytest_deprecation():
    warnings.warn(pytest.PytestDeprecationWarning("planted deprecation"), stacklevel=1)


def test_an_unrelated_warning_stays_a_warning():
    warnings.warn(UserWarning("planted user warning"), stacklevel=1)
"""


def _suite_filters() -> list[str]:
    ini = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return ini["tool"]["pytest"]["ini_options"].get("filterwarnings", [])


def _run(tmp_path, filters: list[str]) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_planted.py").write_text(PLANTED)
    lines = "".join(f"    {f}\n" for f in filters)
    (tmp_path / "pytest.ini").write_text(
        f"[pytest]\nfilterwarnings =\n{lines}" if filters else "[pytest]\n"
    )
    env = hermetic_env(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_PLUGINS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-rA"],
        cwd=tmp_path, env=env, capture_output=True,
        text=True, timeout=patience(120), stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_the_suites_own_filters_make_a_pytest_deprecation_an_error(tmp_path):
    r = _run(tmp_path, _suite_filters())
    out = r.stdout + r.stderr
    assert "FAILED test_planted.py::test_a_pytest_deprecation" in out, out
    assert "PASSED test_planted.py::test_an_unrelated_warning_stays_a_warning" in out, out


def test_without_the_filter_the_same_planted_deprecation_only_warns(tmp_path):
    """Calibration: no filter, no failure -- the red above is the filter's, nothing else's."""
    r = _run(tmp_path, [])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "planted deprecation" in r.stdout, r.stdout


def test_the_filter_is_pytests_own_deprecation_class_only():
    """Not a blanket `error`: the suite's other warnings stay what they were."""
    assert _suite_filters() == ["error::pytest.PytestDeprecationWarning"], _suite_filters()
