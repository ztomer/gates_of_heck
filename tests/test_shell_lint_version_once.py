"""`goh shell-lint` asks `shellcheck --version` once per binary identity (GOH.SCLINTKEY).

Every shell-lint run asked it, its verdicts all cached or not: about 309 uncached execs per goh
suite run. The answer is kept in the verdict store under the binary's path, size and mtime -- the
identity every shell verdict is already keyed on -- so a second run over the same binary execs no
`--version`, a changed binary asks again, and with the store off every run asks.
"""

from __future__ import annotations

import os
from pathlib import Path

from _tool_versions import floor
from conftest import commit_all, run_goh, write

SC_FLOOR = floor("shellcheck")


def _counting_shellcheck(tmp: Path, version: str = SC_FLOOR) -> tuple[Path, Path]:
    """A shellcheck that logs each `--version` it is asked and passes every file."""
    bin_ = tmp / "fake-sc"
    bin_.mkdir(parents=True, exist_ok=True)
    log = tmp / "versions.log"
    sc = bin_ / "shellcheck"
    sc.write_text(
        "#!/bin/bash\n"
        f'if [ "${{1:-}}" = --version ]; then echo asked >> "{log}"\n'
        f'  printf "ShellCheck\\nversion: {version}\\n"; exit 0; fi\n'
        "exit 0\n"
    )
    sc.chmod(0o755)
    return bin_, log


def _asked(log: Path) -> int:
    n = len(log.read_text().splitlines()) if log.is_file() else 0
    log.unlink(missing_ok=True)
    return n


def _env(bin_: Path, store: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    env.update(GOH_VERDICT_DIR=str(store), PATH=f"{bin_}{os.pathsep}{env['PATH']}", **extra)
    return env


def test_a_known_binary_is_asked_its_version_once(repo, tmp_path) -> None:
    for name in ("a.sh", "b.sh"):
        write(repo, name, "#!/usr/bin/env bash\necho hi\n")
    commit_all(repo)
    bin_, log = _counting_shellcheck(tmp_path)
    env = _env(bin_, tmp_path / "verdicts")
    for _ in range(2):
        r = run_goh(repo, "shell-lint", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
    assert _asked(log) == 1, "a second run over the same binary asked its version again"
    write(repo, "a.sh", "#!/usr/bin/env bash\necho changed\n")  # a verdict miss: still asks none
    commit_all(repo)
    assert run_goh(repo, "shell-lint", env=env).returncode == 0
    assert _asked(log) == 0


def test_a_changed_binary_or_an_off_store_asks_again(repo, tmp_path) -> None:
    write(repo, "a.sh", "#!/usr/bin/env bash\necho hi\n")
    commit_all(repo)
    bin_, log = _counting_shellcheck(tmp_path)
    env = _env(bin_, tmp_path / "verdicts")
    assert run_goh(repo, "shell-lint", env=env).returncode == 0
    assert _asked(log) == 1
    sc = bin_ / "shellcheck"
    sc.write_text(sc.read_text() + "# another build\n")  # another size: another binary
    assert run_goh(repo, "shell-lint", env=env).returncode == 0
    assert _asked(log) == 1, "a changed binary kept the old binary's version"
    off = _env(bin_, tmp_path / "verdicts", GOH_VERDICT_CACHE="0")
    for _ in range(2):
        assert run_goh(repo, "shell-lint", env=off).returncode == 0
    assert _asked(log) == 2
