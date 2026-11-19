"""A proven step whose closure runs a script no reading places is RUN, never reused.

GOH.STEPCLOSUREREFUSE. GOH.RUNRULEROOTS made the coverage key refuse (LookupError) a script path
it could not place through a declared root; the proven STEP closure (lib/run_rule.py run_closure,
every proven gate step's key) still skipped one without a word. On the real tree
gates/_goh_bin.sh's `bash "$here/../scripts/build-goh.sh"` was that skip, and scripts/ was no
step part: an edit to the builder every step's runner may run reused a verdict other code made.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import run_rule
import rust_cov_scripts
import step_closure as sc

# The runner's closure spans goh's code parts and the builder it runs.
TREE_READS = ("gates/", "lib/", "checks/", "tools/", "tui/", "scripts/")

PLACED = 'python3 "$GOH_DIR/checks/check_a.py"'
HOLE = 'bash "$GOH_DIR/gates/s.sh"'


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _fresh() -> None:
    for f in (sc._hashed, sc.runner_closure, sc._code, sc.candidates):
        f.cache_clear()


def _lines(goh: Path, repo: Path, steps: list[str], cache: Path | None = None) -> dict[str, str]:
    _fresh()
    out = sc.closures([goh], steps, None, repo, cache, "tree1" if cache else "")
    return {ln.split("\t", 1)[1]: ln.split("\t", 1)[0] for ln in out[1:]}


def _goh(root: Path, runner: str = "#!/usr/bin/env bash\n") -> Path:
    _write(root, {
        "gates/local_ci.sh": runner,
        "gates/proven.sh": "#!/usr/bin/env bash\n",
        "checks/check_a.py": "A = 1\n",
        # A run through a variable no declared root names: its file is decided at run time.
        "gates/s.sh": '#!/usr/bin/env bash\nbash "$ELSEWHERE/x.sh"\n',
    })  # fmt: skip
    return root


def test_a_step_running_an_unplaceable_script_refuses_its_key(tmp_path) -> None:
    goh, repo = _goh(tmp_path / "goh"), tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(LookupError, match=r"gates/s\.sh: \$ELSEWHERE/x\.sh"):
        sc.step_closure(HOLE, repo, goh)
    first = _lines(goh, repo, [PLACED, HOLE], tmp_path / "cache")
    again = _lines(goh, repo, [PLACED, HOLE], tmp_path / "cache")
    assert first[HOLE].startswith("closure refused"), first
    assert first[HOLE] != again[HOLE], "a refused step's key matched a record: it would be reused"
    # The step the reader places keeps its key, and the cache serves it again.
    assert first[PLACED].startswith("closure ") and first[PLACED] == again[PLACED], first


def test_a_runner_hole_refuses_every_step(tmp_path) -> None:
    goh = _goh(tmp_path / "goh", '#!/usr/bin/env bash\nbash "$ELSEWHERE/y.sh"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = _lines(goh, repo, [PLACED])
    assert lines[PLACED].startswith("closure refused"), lines


def test_a_declared_hole_is_followed_by_its_tail_not_refused(tmp_path) -> None:
    """gates/local_ci.sh's `$GOH_GIT_ROOT/tui/lib.sh` is the gated repo's own (its tree keys it) or
    goh's, which the closure keys by the tail."""
    goh = _goh(tmp_path / "goh", '#!/usr/bin/env bash\n. "${GOH_GIT_ROOT:-}/tui/lib.sh"\n')
    _write(goh, {"tui/lib.sh": "# shellcheck shell=bash\n"})
    repo = tmp_path / "repo"
    repo.mkdir()
    assert "gates/local_ci.sh: $GOH_GIT_ROOT/tui/lib.sh" in run_rule.DECLARED
    files, why = sc.step_closure(PLACED, repo, goh)
    assert "tui/lib.sh" in files and not why, files


def test_a_run_of_an_absent_file_is_no_hole_and_keyed_once_it_exists(tmp_path) -> None:
    """A placed path to no file (a fixture without scripts/) is no edge, not a refusal: the closure
    is read from the next tree again, so the file is keyed the moment it exists."""
    goh = _goh(tmp_path / "goh", '#!/usr/bin/env bash\nbash "$HERE/later.sh"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    before = _lines(goh, repo, [PLACED])
    _write(goh, {"gates/later.sh": "#!/usr/bin/env bash\n"})
    after = _lines(goh, repo, [PLACED])
    assert not before[PLACED].startswith("closure refused"), before
    assert after[PLACED] != before[PLACED], "the file that appeared is outside the step's key"


def test_the_builder_goh_bin_runs_is_keyed_in_every_step(tmp_path) -> None:
    """`$here` in gates/_goh_bin.sh is its own directory (run_rule.FILE_ROOTS): the builder it runs
    is in the runner's closure, and scripts/ is a step part, so an edit to it moves every step."""
    runner = '#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/_goh_bin.sh"\n'
    goh = _goh(tmp_path / "goh", runner)
    _write(goh, {
        "gates/_goh_bin.sh": 'here="${live:-$me}/gates"\nbash "$here/../scripts/build-goh.sh"\n',
        "scripts/build-goh.sh": "#!/usr/bin/env bash\necho one\n",
    })  # fmt: skip
    repo = tmp_path / "repo"
    repo.mkdir()
    before = _lines(goh, repo, [PLACED])
    (goh / "scripts/build-goh.sh").write_text("#!/usr/bin/env bash\necho two\n")
    after = _lines(goh, repo, [PLACED])
    assert not before[PLACED].startswith("closure refused"), before
    assert after[PLACED] != before[PLACED], "an edit to the builder reused the step's verdict"


def test_the_real_runner_closure_skips_no_run() -> None:
    """The real tree: the runner's closure (in every proven step's key) places every script path
    it meets, or DECLARED names it; the builder is keyed."""
    _fresh()
    files = sc.runner_closure(REPO_ROOT)
    assert "scripts/build-goh.sh" in files
    assert "scripts" in sc.STEP_PARTS


def test_every_file_root_is_still_used() -> None:
    """A ratchet: a FILE_ROOTS entry whose file no longer runs a script through it fails."""
    for rel, names in run_rule.FILE_ROOTS.items():
        forms = [f for f, _ in run_rule.script_paths(REPO_ROOT / rel, REPO_ROOT)]
        for name in names:
            assert any(f.startswith(f"${name}/") for f in forms), f"{rel}: ${name} runs nothing"


def test_every_declared_hole_is_still_in_the_tree() -> None:
    """A ratchet: a run_rule.DECLARED form its file no longer holds as a hole fails."""
    gone = []
    for entry in run_rule.DECLARED:
        rel, form = entry.split(": ", 1)
        holes = {f for f, t in run_rule.script_paths(REPO_ROOT / rel, REPO_ROOT) if t is None}
        gone += [entry] if form not in holes else []
    assert not gone, "DECLARED holes the tree no longer has: " + "; ".join(gone)


# Two of goh's own GOH_CI_STEPS (.gatesrc), whose closures met every hole DECLARED for them (the
# rest of the 9 met none on 2026-11-19, 10 s to walk all).
GOH_STEPS = ("./gates/rust_gate.sh", "./gates/structural.sh --full")


def test_no_step_of_goh_s_own_refuses() -> None:
    """The real tree: every hole goh's own steps meet is DECLARED, so none is refused a key."""
    _fresh()
    refused = []
    for step in GOH_STEPS:
        try:
            sc.step_closure(step, REPO_ROOT, REPO_ROOT)
        except LookupError as e:
            refused.append(f"{step}: {e}")
    assert not refused, "\n".join(refused)


def test_the_coverage_key_shares_the_run_rules_declarations() -> None:
    for form, why in run_rule.DECLARED.items():
        assert rust_cov_scripts.DECLARED[form] == why, form
    assert "gates/_goh_bin.sh: $here/../scripts/build-goh.sh" not in rust_cov_scripts.DECLARED


def test_another_goh_dir_keys_the_runner_on_its_own_files(tmp_path) -> None:
    """gates/_rust_proven.sh, GOH_DIR another checkout: the runner's `$GOH_DIR` reads resolve into
    THAT checkout, so its line keys that checkout's files. Extra lines only ever add a miss: the
    first identity (goh = the repo) still keys the repo's own copy."""
    repo, other = tmp_path / "repo", tmp_path / "other"
    runner = {"tools/run.sh": '#!/usr/bin/env bash\n. "$GOH_DIR/lib/x.sh"\n',
              "lib/x.sh": "# shellcheck shell=bash\nX=1\n"}  # fmt: skip
    _write(repo, runner)
    _write(other, runner)
    ext = "./tools/run.sh"
    assert "lib/x.sh" in rust_cov_scripts.runner_files(repo, ext, repo)
    assert rust_cov_scripts.runner_goh_files(repo, ext, repo) == []
    assert rust_cov_scripts.runner_goh_files(repo, ext, other) == ["lib/x.sh"]
