"""`goh credential-urls` reads a URL the way `urlsplit` does (Phase N1).

The measured table in `checks/_credential_urls_probe.py` is the spec. Every URL row goes through
both -- kind, host and the fingerprint of the userinfo, which proves the two split the URL at the
same `@` -- and every row, URL and config alike, is planted in a real repository and the whole
report compared, text and `--json`, with the global config sealed off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, git

sys.path.insert(0, str(REPO_ROOT / "checks"))
from _credential_urls_probe import CONFIG_TABLE, TABLE  # noqa: E402
from check_no_credential_urls import fingerprint, verdict  # noqa: E402

CHECK = REPO_ROOT / "checks" / "check_no_credential_urls.py"
EXTRA = [
    ("ipv6 with userinfo", "https://u:p@[::1]:8080/o/r"),
    ("upper-case host", "https://u:p@GitHub.COM/o/r"),
    ("leading space and tab", " \thttps://u:p@host/o"),
    ("query before userinfo", "https://host/o?x=a@b"),
    ("scheme digits", "git+ssh://gho_" + "d" * 36 + "@host/o"),
]


def _native_verdict(goh: Path, url: str):
    r = subprocess.run(
        [str(goh), "credential-urls", "--verdict", url], capture_output=True, text=True
    )
    return json.loads(r.stdout)


@pytest.mark.parametrize(("label", "url"), [(l, u) for l, u, _ in TABLE] + EXTRA)
def test_every_url_row_splits_the_same(goh: Path, label: str, url: str) -> None:
    ref = verdict(url)
    want = None if ref is None else [ref[0], ref[1], fingerprint(ref[2])]
    assert _native_verdict(goh, url) == want, label


def _sealed() -> dict[str, str]:
    return dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def both(goh: Path, repo: Path, *args: str):
    env = _sealed()
    run = lambda argv: subprocess.run(argv, cwd=repo, capture_output=True, text=True, env=env)  # noqa: E731
    py = run([sys.executable, str(CHECK), *args])
    rs = run([str(goh), "credential-urls", *args])
    return (py.returncode, py.stdout, py.stderr), (rs.returncode, rs.stdout, rs.stderr)


@pytest.mark.parametrize("args", [[], ["--json"]], ids=["text", "json"])
def test_a_repo_holding_every_row_reports_the_same(goh: Path, repo: Path, args) -> None:
    for i, (_, url, _) in enumerate(TABLE):
        git(repo, "config", f"remote.r{i}.url", url)
    git(repo, "config", "remote.my.dotted.pushurl", "https://u:gho_" + "e" * 36 + "@h/o")
    git(repo, "config", "url.https://t:x@proxy.invalid/.insteadOf", "https://github.com/")
    for i, (key, value, _) in enumerate(CONFIG_TABLE):
        section, var = key.split(".")
        git(repo, "config", "--add", f"{section}.https://u:p@h{i}.invalid/.{var}", value)
    py, rs = both(goh, repo, *args)
    assert py[0] == 1, py
    assert rs == py


@pytest.mark.parametrize("args", [[], ["--json"]], ids=["text", "json"])
def test_a_clean_repo_and_a_bare_one_report_the_same(goh: Path, repo: Path, args) -> None:
    py, rs = both(goh, repo, *args)
    assert rs == py and py[0] == 0, py
    git(repo, "config", "remote.origin.url", "https://github.com/o/r.git")
    py, rs = both(goh, repo, *args)
    assert rs == py and py[0] == 0, py
