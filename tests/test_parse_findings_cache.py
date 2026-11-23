"""tests/_parse_findings.py keeps each file's parse findings by what shellcheck could read
(GOH.SHELLCHECKCACHE).

tests/test_shell_lint_follows_sources.py parsed every tracked shell file of goh on every suite run
(74 s under load; 7-15 s for one file, which re-reads every library it sources). The findings of
one file are a function of shellcheck's identity, the flags, the file and the closure of files it
can source, and the .shellcheckrc files above it: that is the key. A repeat with the same key
execs nothing; an edit to a sourced library re-parses only the files that reach it. Here a fake
shellcheck on PATH counts the execs.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import _parse_findings as pf
from _fast_git import fast_init

FAKE = """#!/usr/bin/env bash
# a stand-in shellcheck: logs one line per exec and names each file argument that says BAD
for a in "$@"; do
    case "$a" in -*) continue ;; esac
    echo "$a" >> "$FAKE_LOG"
    if grep -q BAD "$a"; then echo "$a:1:1: note: bad [SC1091]"; fi
done
exit 0
"""


def _repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "shellcheck"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "execs.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv(pf.CACHE_ENV, str(tmp_path / "cache"))
    repo = tmp_path / "repo"
    (repo / "lib").mkdir(parents=True)
    (repo / "lib" / "common.sh").write_text("x=1\n")
    (repo / "a.sh").write_text('. "$(dirname "$0")/lib/common.sh"\necho BAD\n')
    (repo / "b.sh").write_text("echo b\n")
    (repo / ".shellcheckrc").write_text("source-path=SCRIPTDIR\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    fast_init(repo)
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, env=env, stdin=subprocess.DEVNULL
    )
    return repo, log


def _execd(log: Path) -> list[str]:
    lines = log.read_text().split() if log.is_file() else []
    log.unlink(missing_ok=True)
    return sorted(lines)


def test_a_repeat_with_the_same_inputs_execs_no_shellcheck(tmp_path, monkeypatch) -> None:
    repo, log = _repo(tmp_path, monkeypatch)
    first = pf.findings(["a.sh", "b.sh"], repo, "--include=SC1091")
    assert first == ["a.sh:1:1: note: bad [SC1091]"]
    assert _execd(log) == ["a.sh", "b.sh"]
    assert pf.findings(["a.sh", "b.sh"], repo, "--include=SC1091") == first
    assert _execd(log) == [], "the same inputs ran shellcheck again"


def test_an_edit_to_a_sourced_library_reparses_only_its_sourcers(tmp_path, monkeypatch) -> None:
    repo, log = _repo(tmp_path, monkeypatch)
    pf.findings(["a.sh", "b.sh"], repo, "--include=SC1091")
    _execd(log)
    (repo / "lib" / "common.sh").write_text("x=2\n")
    assert pf.findings(["a.sh", "b.sh"], repo, "--include=SC1091") == [
        "a.sh:1:1: note: bad [SC1091]"
    ]
    assert _execd(log) == ["a.sh"]
    (repo / ".shellcheckrc").write_text("source-path=SCRIPTDIR\ndisable=SC2034\n")
    pf.findings(["a.sh", "b.sh"], repo, "--include=SC1091")
    assert _execd(log) == ["a.sh", "b.sh"], "an rc edit kept a verdict"
    pf.findings(["a.sh", "b.sh"], repo, "--severity=info")
    assert _execd(log) == ["a.sh", "b.sh"], "other flags kept a verdict"


def test_a_file_newly_naming_a_missing_source_is_reparsed(tmp_path, monkeypatch) -> None:
    """A source that appears later is a new edge: the file that names it misses."""
    repo, log = _repo(tmp_path, monkeypatch)
    (repo / "b.sh").write_text('. "$(dirname "$0")/later.sh"\n')
    pf.findings(["b.sh"], repo)
    _execd(log)
    (repo / "later.sh").write_text("BAD=1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    pf.findings(["b.sh"], repo)
    assert _execd(log) == ["b.sh"]


def test_outside_a_repository_or_the_suite_nothing_is_kept(tmp_path, monkeypatch) -> None:
    repo, log = _repo(tmp_path, monkeypatch)
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "p.sh").write_text("echo BAD\n")
    for _ in range(2):
        assert pf.findings(["p.sh"], plain) == ["p.sh:1:1: note: bad [SC1091]"]
    assert _execd(log) == ["p.sh", "p.sh"], "the probe's tree must be parsed every time"
    monkeypatch.delenv(pf.CACHE_ENV)
    monkeypatch.delenv(pf.suite_home.MARKER, raising=False)
    for _ in range(2):
        pf.findings(["b.sh"], repo)
    assert _execd(log) == ["b.sh", "b.sh"]
