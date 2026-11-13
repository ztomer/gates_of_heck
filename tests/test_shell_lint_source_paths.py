"""What a shell file names as a source must be a file the lint can see (GOH.SHX2).

`goh shell-lint` runs `shellcheck -x` in the tree a verdict is about (GOH.SHX), so every file a
script makes shellcheck read has to BE in that tree and in the verdict's cache key. Three ways a
file escaped it, one test each:

* an ABSOLUTE `# shellcheck source=/Users/...` path: shellcheck read one machine's file, outside
  the tree, outside the staged checkout and outside the key -- and on any other machine it reads
  nothing. Refused: a source is repo-relative, or `/dev/null` with the reason beside it;
* a `$VAR/` path to a file that is not shell: shellcheck strips the leading variable and follows
  the rest from its working directory, but the lint collected only literal paths, so a staged run
  never checked that file out of the index and the cache key never covered it;
* goh's own gates following goh's `.gatesrc` under `-x`: the file they source is the GATED repo's,
  unknown when the gate is linted, so they say `source=/dev/null`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _parse_findings import findings, probe
from conftest import commit_all, run_goh, stage, write

pytestmark = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")

GOH = Path(__file__).resolve().parent.parent

LIB = '#!/usr/bin/env bash\nhelper() {\n  echo "$1"\n}\n'


def _lint(repo: Path, *args: str, cache: Path | None = None):
    env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    if cache is None:
        env["GOH_VERDICT_CACHE"] = "0"
    else:
        env["GOH_VERDICT_DIR"] = str(cache)
    r = run_goh(repo, "shell-lint", *args, env=env)
    return r.returncode, r.stdout + r.stderr


# ── (a) an absolute source= directive is refused ─────────────────────────────


def _main_with(directive: str) -> str:
    return f'#!/usr/bin/env bash\n{directive}\n. "$HERE/lib.sh"\nhelper a\n'


def test_an_absolute_source_directive_is_refused_at_full_scope(repo, tmp_path):
    outside = write(tmp_path, "elsewhere/lib.sh", LIB)
    write(repo, "main.sh", _main_with(f"# shellcheck source={outside}"))
    commit_all(repo)
    rc, out = _lint(repo)
    assert rc == 1, out
    assert f"main.sh:2: shellcheck source={outside}" in out, out
    assert "repo-relative" in out, out


def test_an_absolute_source_directive_is_refused_when_staged(repo, tmp_path):
    outside = write(tmp_path, "elsewhere/lib.sh", LIB)
    write(repo, "main.sh", _main_with(f"#  shellcheck disable=SC2154 source={outside}"))
    stage(repo, "main.sh")
    rc, out = _lint(repo, "--staged")
    assert rc == 1, out
    assert f"source={outside}" in out, out


def test_a_cached_verdict_does_not_hide_an_absolute_directive(repo, tmp_path):
    outside = write(tmp_path, "elsewhere/lib.sh", LIB)
    write(repo, "main.sh", _main_with(f"# shellcheck source={outside}"))
    commit_all(repo)
    cache = tmp_path / "cache"
    first, _ = _lint(repo, cache=cache)
    rc, out = _lint(repo, cache=cache)
    assert (first, rc) == (1, 1), out


@pytest.mark.parametrize(
    "directive",
    ["# shellcheck source=lib.sh", "# shellcheck source=/dev/null"],
)
def test_a_repo_relative_or_dev_null_directive_passes(repo, directive):
    write(repo, "lib.sh", LIB)
    write(repo, "main.sh", _main_with(directive))
    commit_all(repo)
    rc, out = _lint(repo)
    assert rc == 0, out


# ── (c) a non-shell file named through `$VAR/` is collected ──────────────────

CONF_PLAIN = "X=1\n"
CONF_ARRAY = "X=1\nitems=(a b)\n"
VIA_VAR = '#!/usr/bin/env bash\nD=.\n. "$D/conf"\nitems="s"\necho "$items $X"\n'


def test_a_staged_script_reads_a_var_named_config_from_the_index(repo):
    write(repo, "conf", CONF_PLAIN)
    commit_all(repo)
    write(repo, "conf", CONF_ARRAY)
    write(repo, "main.sh", VIA_VAR)
    stage(repo, "conf", "main.sh")
    write(repo, "conf", CONF_PLAIN)  # the worktree is clean; what is being committed is not
    rc, out = _lint(repo, "--staged")
    assert rc == 1, out
    assert "main.sh#SC2178" in out, out


def test_a_cached_verdict_moves_when_a_var_named_config_moves(repo, tmp_path):
    cache = tmp_path / "cache"
    write(repo, "conf", CONF_PLAIN)
    write(repo, "main.sh", VIA_VAR)
    commit_all(repo)
    rc, out = _lint(repo, cache=cache)
    assert rc == 0, out
    write(repo, "conf", CONF_ARRAY)  # main.sh's bytes are unchanged
    commit_all(repo)
    rc, out = _lint(repo, cache=cache)
    assert rc == 1, out
    assert "main.sh#SC2178" in out, out


# ── (b) goh's own gates never follow goh's .gatesrc ──────────────────────────


def _shell_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(GOH), "ls-files", "-z"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.decode()
    return [
        f
        for f in out.split("\0")
        if f
        and (f.endswith(".sh") or (f.startswith("hooks/") and "." not in f.rsplit("/", 1)[-1]))
        and (GOH / f).is_file()
    ]


def test_no_goh_shell_file_makes_shellcheck_follow_a_gatesrc(tmp_path):
    # Run beside a .gatesrc shellcheck cannot parse: a source it FOLLOWS is named by SC1094 (and by
    # SC1091 where no file is found); one declared source=/dev/null by neither. Behavioural, so a new
    # spelling is caught too -- and `disable=SC1091` is not mistaken for not following
    # (local_ci.sh carried exactly that and followed goh's own .gatesrc anyway).
    (tmp_path / ".gatesrc").write_text("if then (\n")
    named = [rel for rel in _shell_files() if ".gatesrc" in (GOH / rel).read_text(errors="replace")]
    assert any("SC1091" in line for line in probe(tmp_path / "probe", "--severity=info")), (
        "the instrument cannot see a missing source"
    )
    # Only parsed, in parallel (tests/_parse_findings.py): both codes are parse findings. Each line
    # is judged against the file it names, whichever script's run reported it.
    followed = []
    for line in findings([str(GOH / rel) for rel in named], tmp_path, "--severity=info"):
        if "SC1091" not in line and "SC1094" not in line:
            continue
        path, lineno = line.split(":")[:2]
        src = Path(path) if Path(path).is_absolute() else tmp_path / path
        text = src.read_text(errors="replace").splitlines() if src.is_file() else []
        if 0 < int(lineno) <= len(text) and ".gatesrc" in text[int(lineno) - 1]:
            rel = src.relative_to(GOH) if src.is_relative_to(GOH) else src
            followed.append(f"{rel}:{lineno}: {text[int(lineno) - 1].strip()}")
    assert followed == [], "\n".join(followed)
