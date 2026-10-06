"""`goh shell-lint` reuses a file's verdict only when nothing it depends on moved (BACKLOG P4).

Shell lint was the pole of a structural run (media_server `--full`: 1.57 of 1.9 s, all of it
shellcheck). Its verdict is a pure function of the file's bytes and path, both tools, the flags,
`SHELLCHECK_OPTS` and every `.shellcheckrc` shellcheck would read -- so the key names all of them,
and each way a hit could be wrong is planted here. A logging `shellcheck` on PATH says which files
the tool was really asked about.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import git, write

pytestmark = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")

CLEAN = "#!/usr/bin/env bash\necho hi\n"
RED = "#!/usr/bin/env bash\nprintf '%s' $@\n"  # SC2068, an error-severity finding


@pytest.fixture
def lint(repo: Path, tmp_path: Path, goh: Path):
    real = shutil.which("shellcheck")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "shellcheck.log"
    fake = bindir / "shellcheck"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a" >> {log}; done\nexec {real} "$@"\n')
    fake.chmod(0o755)
    write(repo, "a.sh", CLEAN)
    write(repo, "b.sh", RED)
    git(repo, "add", "-A")

    def run(**extra: str):
        log.write_text("")
        env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
        env.update(PATH=f"{bindir}:{os.environ['PATH']}", GOH_VERDICT_DIR=str(tmp_path / "cache"))
        env.update(extra)
        r = subprocess.run(
            [str(goh), "shell-lint"], cwd=repo, capture_output=True, text=True, env=env
        )
        asked = {Path(a).name for a in log.read_text().split() if a.endswith(".sh")}
        return (r.returncode, r.stdout, r.stderr), asked

    run.fake = fake
    run.cache = tmp_path / "cache"
    return run


def test_a_warm_run_says_the_same_and_asks_shellcheck_nothing(lint) -> None:
    cold, asked = lint()
    assert cold[0] == 1 and "b.sh" in cold[2] and "SC2068" in cold[2], cold
    assert asked == {"a.sh", "b.sh"}, asked
    warm, asked = lint()
    assert warm == cold
    assert asked == set(), f"a warm run re-asked shellcheck about {asked}"


def test_an_edited_file_is_judged_again_and_only_it(lint, repo: Path) -> None:
    lint()
    # The SAME size: a key on size or mtime alone would serve the old verdict.
    write(repo, "a.sh", CLEAN.replace("hi", "ho"))
    _, asked = lint()
    assert asked == {"a.sh"}, asked


def test_a_shellcheckrc_shellcheck_would_read_rejudges_everything(lint, repo: Path) -> None:
    cold, _ = lint()
    write(repo, ".shellcheckrc", "disable=SC2068\n")
    after, asked = lint()
    assert asked == {"a.sh", "b.sh"}, asked
    assert after[0] == 0, f"the rc file's verdict was not the one reported: {after}"


def test_shellcheck_opts_rejudge(lint) -> None:
    lint()
    _, asked = lint(SHELLCHECK_OPTS="--exclude=SC2068")
    assert asked == {"a.sh", "b.sh"}, asked


def test_a_different_shellcheck_rejudges(lint) -> None:
    lint()
    os.utime(lint.fake, (1, 1))
    _, asked = lint()
    assert asked == {"a.sh", "b.sh"}, asked


def test_a_corrupt_record_is_a_miss_not_a_verdict(lint) -> None:
    cold, _ = lint()
    records = list(lint.cache.rglob("*.json"))
    assert len(records) == 2, records
    for r in records:
        r.write_text("{not json")
    again, asked = lint()
    assert asked == {"a.sh", "b.sh"} and again == cold, (asked, again)


def test_the_cache_off_runs_everything(lint) -> None:
    lint()
    _, asked = lint(GOH_VERDICT_CACHE="0")
    assert asked == {"a.sh", "b.sh"}, asked
