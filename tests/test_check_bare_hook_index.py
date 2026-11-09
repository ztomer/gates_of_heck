"""The hook's carried index is read only by `goh_bind_hook_index` (`crates/goh/src/hookindex/`).

routines' and media_server's tools/gate.sh read `${GOH_HOOK_INDEX_FILE:-...}` bare, so a gate a
test ran on a FIXTURE under a `commit -a` hook bound the outer commit's index and died "unable to
read 404acec" (2026-10-08, contract #12). What an expansion is lives beside the code
(`hookindex/tests.rs`, mutation-proven); this file is the whole-repo behaviour: the incident as
written, index truth, the binder's own file, exclusion, the dispatcher, and the structural step.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, run_goh, write

LABEL = "no bare read of the hook's index"
# routines' tools/gate.sh line 33 before 852b8d5.
BARE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'staged="$(GIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-$(git rev-parse --git-path index)}" '
    'git diff --cached --name-only --diff-filter=d)"\n'
)
# The same line after it.
BOUND = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'staged="$(. "$GOH/gates/_git_env.sh"; goh_bind_hook_index; git diff --cached --name-only)"\n'
)


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_incident_is_named_at_its_line(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tools/gate.sh:3:" in r.stderr and "$GOH_HOOK_INDEX_FILE" in r.stderr, r.stderr
    assert "goh_bind_hook_index" in r.stderr, r.stderr  # the fix is named


def test_the_fixed_line_passes(repo: Path) -> None:
    write(repo, "tools/gate.sh", BOUND)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 0 and "1 tracked shell source" in r.stdout, r.stdout + r.stderr


def test_staged_judges_the_index_not_the_worktree(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    git(repo, "add", "tools/gate.sh")
    write(repo, "tools/gate.sh", BOUND)  # the worktree is clean; the commit is not
    r = run_goh(repo, "bare-hook-index", "--staged")
    assert r.returncode == 1 and "tools/gate.sh:3:" in r.stderr, r.stdout + r.stderr
    assert run_goh(repo, "bare-hook-index").returncode == 0  # the worktree's own verdict


def test_the_binders_own_file_is_its_one_reader(repo: Path) -> None:
    """gates/_git_env.sh reads both variables -- it is where they are bound. The exemption is the
    DEFINITION, not a path: this repo's own copy passes, and so does a consumer's that defines it."""
    write(repo, "lib/env.sh", (REPO_ROOT / "gates" / "_git_env.sh").read_text())
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 0, r.stdout + r.stderr


def test_goh_exclude_applies(repo: Path) -> None:
    write(repo, "vendor/gate.sh", BARE)
    write(repo, ".gatesrc", "GOH_EXCLUDE='^vendor/'\n")
    commit_all(repo)
    assert run_goh(repo, "bare-hook-index").returncode == 0
    assert run_goh(repo, "bare-hook-index", "--exclude", "").returncode == 1


def test_the_dispatcher_knows_it(repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    commit_all(repo)
    r = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "goh.sh"), "bare-hook-index"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 1 and "unknown check" not in r.stderr, r.stdout + r.stderr


def test_the_structural_step_fails_at_both_scopes(goh: Path, repo: Path) -> None:
    write(repo, "tools/gate.sh", BARE)
    git(repo, "add", "tools/gate.sh")
    staged = _structural(goh, repo, "--staged")
    assert staged.returncode == 1 and f"structural: {LABEL}" in staged.stderr, staged.stderr
    commit_all(repo)
    full = _structural(goh, repo, "--full")
    assert full.returncode == 1 and f"structural: {LABEL}" in full.stderr, full.stderr


# GOH.BAREINDEX: an index read that never binds the hook's index. .loop's tools/gate.sh before
# 50ff65b: under `git commit -- queue.json` the hook commits a TEMPORARY index, a plain
# `git diff --cached` read the repository's own, and the queue gate never ran.
PLAIN = '#!/usr/bin/env bash\nset -euo pipefail\nstaged="$(git diff --cached --name-only)"\n'
# .loop's line after it.
LOOP_BOUND = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    '. "$GOH/gates/_git_env.sh"\n'
    'staged="$( (goh_bind_hook_index; git diff --cached --name-only) )"\n'
)
INDEX_LABEL = "index read(s) in a hook-run script that never binds the hook's index"


def test_a_plain_index_read_in_the_gate_is_red_at_its_line(repo: Path) -> None:
    write(repo, "tools/gate.sh", PLAIN)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 1, r.stdout + r.stderr
    assert INDEX_LABEL in r.stderr and "tools/gate.sh:3:" in r.stderr, r.stderr
    assert "git diff --cached without goh_bind_hook_index" in r.stderr, r.stderr


def test_the_same_read_bound_is_green(repo: Path) -> None:
    write(repo, "tools/gate.sh", LOOP_BOUND)
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_script_the_gate_runs_is_hook_run_and_one_it_does_not_is_not(repo: Path) -> None:
    """koffee_big's shape: tools/gate.sh runs `"$(dirname "$0")/run_gates.sh"`, which reads the
    index. A release script nothing in the hook names is judged by nobody."""
    write(repo, "tools/gate.sh", '#!/usr/bin/env bash\n"$(dirname "$0")/run_gates.sh" "$@"\n')
    write(repo, "tools/run_gates.sh", "#!/usr/bin/env bash\ngit show :queue.json >/dev/null\n")
    write(repo, "release.sh", "#!/usr/bin/env bash\ngit diff --cached --quiet || exit 1\n")
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    assert r.returncode == 1 and "tools/run_gates.sh:2:" in r.stderr, r.stdout + r.stderr
    assert "release.sh" not in r.stderr, r.stderr


def test_every_index_read_shape_is_named(repo: Path) -> None:
    write(
        repo,
        "tools/gate.sh",
        "#!/usr/bin/env bash\n"
        "git -C . diff --staged --quiet\n"
        "git show ':a.json'\n"
        "git ls-files -sz\n"
        "git write-tree\n"
        "git ls-files --others  # lists, reads no stage\n"
        "# git diff --cached in a comment\n",
    )
    commit_all(repo)
    r = run_goh(repo, "bare-hook-index")
    for line in ("tools/gate.sh:2:", "tools/gate.sh:3:", "tools/gate.sh:4:", "tools/gate.sh:5:"):
        assert line in r.stderr, r.stderr
    assert "tools/gate.sh:6:" not in r.stderr and "tools/gate.sh:7:" not in r.stderr, r.stderr
    assert "4 index read(s)" in r.stderr, r.stderr


def test_a_private_index_the_script_chose_is_not_the_hooks(repo: Path) -> None:
    """ZoneWM's release_precheck.sh shape: GIT_INDEX_FILE names a copy it made on purpose."""
    write(
        repo,
        "tools/gate.sh",
        '#!/usr/bin/env bash\nidx="$(mktemp "${TMPDIR:-/tmp}/goh-idx.XXXXXX")"\ntree="$(GIT_INDEX_FILE="$idx" git write-tree)"\n',
    )
    commit_all(repo)
    assert run_goh(repo, "bare-hook-index").returncode == 0


def _seeded_goh(tmp: Path, root: str, count: int) -> Path:
    """A private seed store (GOH_SEED_DIR) holding the fixture's entry (GOH.SEEDPRIVATE)."""
    goh = tmp / "seeds"
    goh.mkdir()
    entry = {"root": root, "repo": "fixture", "path": "tools/gate.sh", "count": count}
    seed = {"why": "a test's", "entries": [{**entry, "reason": "unreviewed: a test's"}]}
    (goh / "hook_index_seed.json").write_text(json.dumps(seed))
    return goh


def _under(goh: Path, repo: Path) -> subprocess.CompletedProcess:
    return run_goh(repo, "bare-hook-index", env=hermetic_env(GOH_SEED_DIR=str(goh)))


def test_the_estate_seed_warns_by_root_commit_in_any_clone(repo: Path, tmp_path: Path) -> None:
    """The seed is keyed by the ROOT COMMIT. Keyed by directory name it missed the release
    canary's clone (named `tree`), and the land was refused for a seeded consumer."""
    write(repo, "tools/gate.sh", PLAIN)
    commit_all(repo)
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    goh = _seeded_goh(tmp_path, root, 1)
    clone = tmp_path / "tree"
    subprocess.run(
        ["git", "clone", "-q", str(repo), str(clone)],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    for where in (repo, clone):
        r = _under(goh, where)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "seeded (GOH.BAREINDEX ratchet): tools/gate.sh" in r.stderr, r.stderr
        assert "tools/gate.sh:3:" in r.stderr, r.stderr  # a warn names its site: never silent
    # Over its count is red; unseeded (the real seed, which does not know this repo) is red.
    write(repo, "tools/gate.sh", PLAIN + "git write-tree\n")
    commit_all(repo)
    assert _under(goh, repo).returncode == 1
    assert run_goh(repo, "bare-hook-index").returncode == 1


def test_a_seeded_file_gone_clean_says_to_delete_its_entry(repo: Path, tmp_path: Path) -> None:
    write(repo, "tools/gate.sh", LOOP_BOUND)
    commit_all(repo)
    goh = _seeded_goh(tmp_path, git(repo, "rev-list", "--max-parents=0", "HEAD").strip(), 1)
    r = _under(goh, repo)
    # Stale is red: the repository that fixed the site deletes its own entry (GOH.SEEDPRIVATE).
    assert r.returncode == 1 and "delete its entry" in r.stderr, r.stdout + r.stderr
