"""A commit's gate never writes the repository it gates (BACKLOG 6.11, the commit half).

The games metarepo's `origin` read `https://elsewhere.invalid/x.git` for days, written into the real
`.git/config` by a probe in a commit's own hook run. The push gate compares the repository's config
and refs before and after its gate; the stock pre-commit now does the same through
gates/commit_gate.sh, pass or fail: a config write refuses the commit naming the key, and a moved
ref is named, not refused.
"""

from __future__ import annotations

from pathlib import Path

from test_commit_gate_structural import CALLS, _commit, _consumer, _git

WROTE = "own .git/config"
URL = "https://elsewhere.invalid/x.git"


def _writing(line: str, then: str = "") -> str:
    return CALLS.replace("exec bash", f"git {line}\n{then}bash")


def test_a_gate_that_writes_the_repos_config_is_refused_and_named(tmp_path: Path) -> None:
    repo = _consumer(tmp_path, _writing(f"config remote.origin.url {URL}"))
    head = _git(repo, "rev-parse", "HEAD")
    got = _commit(repo, tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert WROTE in text and f"remote.origin.url={URL}" in text, text
    assert _git(repo, "rev-parse", "HEAD") == head, "the commit was made over a config write"


def test_a_failing_gate_that_writes_the_config_is_reported_too(tmp_path: Path) -> None:
    repo = _consumer(tmp_path, _writing(f"config remote.origin.url {URL}", then="exit 3\n"))
    got = _commit(repo, tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert WROTE in text and URL in text, text


def test_a_moved_ref_is_named_not_refused(tmp_path: Path) -> None:
    repo = _consumer(tmp_path, _writing("branch backup/flatten-records"))
    got = _commit(repo, tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert "moved refs" in text and "refs/heads/backup/flatten-records" in text, text


def test_a_gate_that_writes_nothing_says_nothing(tmp_path: Path) -> None:
    got = _commit(_consumer(tmp_path, CALLS), tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert WROTE not in text and "moved refs" not in text, text
