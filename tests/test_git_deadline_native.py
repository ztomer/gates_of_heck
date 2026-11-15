"""`goh git-deadline`: the GOH.GITKILL class as a native structural step in every repo
(GOH.GITKILLNATIVE, `crates/goh/src/gitdeadline.rs`).

The class -- a git subprocess SIGKILLed at a deadline strands `index.lock` and orphans its hook; a
work-tree reader without `GIT_OPTIONAL_LOCKS=0` takes that lock too -- was a pytest scan of
gates_of_heck's own tree (`tests/test_git_deadline_class.py`), so `.loop`'s `loopq.py`, with the
same shape, had no check. The shape table is `gitdeadline.rs`'s unit tests; this is the whole-repo
behaviour: a consumer's structural gate goes red on a planted site, the grace keeps it quiet until
`crate::grace::OVER_FROM`, a tracked seed entry warns, the escape and the fix are green.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no git stopped at a deadline holding a lock"
# The incident's shape (`.loop` 4e82af6): a writer under subprocess's timeout=, SIGKILLed alone.
PLANTED = (
    "import subprocess\n\n\ndef land(repo):\n"
    '    subprocess.run(["git", "commit", "-q"], cwd=repo, timeout=30, check=False,\n'
    "                   stdin=subprocess.DEVNULL)\n"
)
FIXED = PLANTED.replace(", timeout=30", "")
ESCAPED = PLANTED.replace("check=False,", "check=False,  # git-deadline-ok: a scratch repo")


def _env(tmp_path: Path, grace: str = "over") -> dict[str, str]:
    return hermetic_env(GOH_DIR=str(REPO_ROOT), GOH_GRACE=grace, GOH_SEED_DIR=str(tmp_path / "s"))


def _check(repo: Path, tmp_path: Path, grace: str = "over") -> subprocess.CompletedProcess:
    return run_goh(repo, "git-deadline", env=_env(tmp_path, grace))


def _structural(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return run_goh(repo, "structural", *args, env=_env(tmp_path))


def test_a_consumer_structural_gate_is_red_on_a_planted_sigkill_at_deadline(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "tools/loopq.py", PLANTED)
    commit_all(repo)
    r = _structural(repo, tmp_path, "--full", "--keep-going")
    assert r.returncode == 1 and f"structural: {LABEL}" in r.stderr, r.stdout + r.stderr
    assert "tools/loopq.py:5:" in r.stderr and "SIGKILLed alone" in r.stderr, r.stderr
    write(repo, "tools/loopq.py", FIXED)
    commit_all(repo)
    r = _structural(repo, tmp_path, "--full", "--keep-going")
    assert f"structural: {LABEL}" not in r.stderr, r.stderr


def test_the_staged_scope_refuses_the_commit_that_brings_one(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/loopq.py", PLANTED)
    git(repo, "add", "-A")
    r = _structural(repo, tmp_path, "--staged")
    assert r.returncode == 1 and f"structural: {LABEL}" in r.stderr, r.stdout + r.stderr
    assert "tools/loopq.py:5:" in r.stderr, r.stderr


def test_within_the_grace_it_reads_and_judges_nothing(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/loopq.py", PLANTED)
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 1, "calibration: the site is red once judged"
    r = _check(repo, tmp_path, grace="within")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_reader_shell_line_and_a_timeout_are_found_and_the_no_lock_forms_pass(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "release.sh", '#!/usr/bin/env bash\nd="$(git status --porcelain)"\n')
    write(repo, "push.sh", "#!/usr/bin/env bash\ntimeout 30 git fetch origin\n")
    # GOH.GITKILLLOCKFREE: ls-remote takes no lock and runs no hook; a bare timeout strands nothing
    write(repo, "lsr.sh", "#!/usr/bin/env bash\ntimeout 30 git ls-remote origin\n")
    write(repo, "ok.sh", '#!/usr/bin/env bash\nd="$(GIT_OPTIONAL_LOCKS=0 git status)"\n')
    # koffee_big tools/cut_release.sh:274/:360: two revisions read no work tree, so no lock
    write(repo, "cut.sh", '#!/usr/bin/env bash\nif git diff --quiet "v$VERSION" HEAD; then :; fi\n')
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "release.sh:2:" in r.stderr and "push.sh:2:" in r.stderr, r.stderr
    assert "ok.sh" not in r.stderr and "cut.sh" not in r.stderr, r.stderr
    assert "lsr.sh" not in r.stderr, r.stderr


def test_the_escape_and_a_test_file_are_green(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/loopq.py", ESCAPED)
    write(repo, "tests/test_land.py", PLANTED)
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_tracked_seed_entry_warns_and_over_its_count_is_red(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/loopq.py", PLANTED)
    commit_all(repo)
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    entry = {"root": root, "repo": "fixture", "path": "tools/loopq.py", "count": 1}
    seed = {"why": "a test's", "entries": [{**entry, "reason": "unreviewed: a test"}]}
    write(repo, "git_deadline_seed.json", json.dumps(seed))
    commit_all(repo)
    r = _check(repo, tmp_path)
    assert r.returncode == 0 and "tools/loopq.py" in r.stderr, r.stdout + r.stderr
    write(repo, "tools/loopq.py", PLANTED + PLANTED.split("\n\n\n", 1)[1].replace("land", "l2"))
    commit_all(repo)
    assert _check(repo, tmp_path).returncode == 1, "over its count is red"


def test_the_dispatcher_knows_the_check(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/loopq.py", PLANTED)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "git-deadline"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "tools/loopq.py:5:" in r.stderr, r.stdout + r.stderr


def test_this_repo_has_no_site(tmp_path: Path) -> None:
    r = _check(REPO_ROOT, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
