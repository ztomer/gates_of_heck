"""`goh tag-version` reads a version source the way `_version_sources` does (Phase N1).

Every strategy (file, cargo, swift, xcconfig, plist, pyproject) over every fixture text the
strategy suites declare -- harvested from the modules, so a fixture added there is compared here --
plus a few edge texts. The whole-repo tests of `test_check_tag_version*.py` run on both tiers.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

from reference_kit import load_reference  # noqa: E402

sources = load_reference("_version_sources")

MODULES = [
    "test_check_tag_version_plist",
    "test_check_tag_version_pyproject",
    "test_check_tag_version_swift",
    "test_check_tag_version_xcconfig",
    "test_check_tag_version",
]
EDGE = [
    "",
    "\n\n# comment\nv1.2.3\n",
    '[package]\nversion = "1.0.0"\n[dependencies]\nversion = "9"\n',
    '[workspace.package]\nversion = "2.0.0"\n',
    "<key>CFBundleShortVersionString</key>\n<string> 1.2.3 </string>\n",
    "MARKETING_VERSION = 2.73.0 // the release\nCURRENT_PROJECT_VERSION = 131\n",
    'public static let version = "4.5.6"\nlet build = "131"\n',
    '[project]\nversion = "0.4.0"\n[tool.poetry]\nversion = "0.4.1"\n',
]


def _texts() -> list[str]:
    out = list(EDGE)
    for name in MODULES:
        mod = importlib.import_module(name)
        for value in vars(mod).values():
            if isinstance(value, str) and "\n" in value:
                out.append(value.format(version="2.10.0") if "{version}" in value else value)
    return sorted(set(out))


TEXTS = _texts()


@pytest.mark.parametrize("kind", list(sources.KINDS))
def test_every_strategy_reads_every_fixture_the_same(goh: Path, kind: str) -> None:
    assert len(TEXTS) > 10, TEXTS
    for text in TEXTS:
        want = [list(x) for x in sources.STRATEGIES[kind](text)]
        r = subprocess.run(
            [str(goh), "tag-version", "--extract", kind], input=text, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr
        assert json.loads(r.stdout) == want, (kind, text[:80])
