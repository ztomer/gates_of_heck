"""No proven key mode leaves out a file the step runs (GOH.FULLKEYSCRIPTS).

GOH.STEPCLOSUREREFUSE put scripts/ (gates/_goh_bin.sh runs scripts/build-goh.sh) in the step
closure's parts, a list of its own beside gate_identity.PARTS: the whole-content key, which
GOH_PROVEN_CLOSURE=off (the default) and `closure full` both use, still keyed PARTS alone, so an
edit to the builder reused every step verdict under them. There is ONE list of goh's code parts now
(run_rule.CODE_PARTS), which every key mode reads. And four `source` lines of the runner closure
no reading places were skipped without a word: each is declared in run_rule.DECLARED with why it
is safe, and an undeclared one refuses the step its key, as an unplaced run does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import run_rule
import rust_cov_scripts
import source_closure
import step_closure as sc

# The runner's closure reads goh's code parts and the builder it runs.
TREE_READS = ("gates/", "lib/", "checks/", "tools/", "tui/", "scripts/")

PLACED = 'python3 "$GOH_DIR/checks/check_a.py"'
BUILDER = "scripts/build-goh.sh"


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _fresh() -> None:
    for f in (sc._hashed, sc.runner_closure, sc._code, sc.candidates, source_closure.tree_files):
        f.cache_clear()


def _goh(root: Path, runner: str) -> Path:
    _write(root, {
        "gates/local_ci.sh": runner,
        "gates/proven.sh": "#!/usr/bin/env bash\n",
        "checks/check_a.py": "A = 1\n",
        "gates/_goh_bin.sh": 'here="${live:-$me}/gates"\nbash "$here/../scripts/build-goh.sh"\n',
        BUILDER: "#!/usr/bin/env bash\necho one\n",
    })  # fmt: skip
    return root


BIN_RUNNER = '#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/_goh_bin.sh"\n'


def _key(mode: str, goh: Path, repo: Path) -> str:
    """The goh half of a step's proven key under GOH_PROVEN_CLOSURE=`mode`: off is the `goh` lines
    of the run-constant identity (gates/_proven.sh), full the `closure full` line a step the
    closure cannot place is keyed on, on the step's own closure line."""
    _fresh()
    if mode == "off":
        return "\n".join(gate_identity.proven([goh], None, repo, closure=False))
    lines = sc.closures([goh], [PLACED], None, repo)
    return lines[0] if mode == "full" else lines[1]


@pytest.mark.parametrize("mode", ["off", "full", "on"])
def test_an_edit_to_the_builder_moves_the_step_key_in_every_mode(tmp_path, mode) -> None:
    goh, repo = _goh(tmp_path / "goh", BIN_RUNNER), tmp_path / "repo"
    repo.mkdir()
    before = _key(mode, goh, repo)
    (goh / BUILDER).write_text("#!/usr/bin/env bash\necho two\n")
    after = _key(mode, goh, repo)
    assert "closure refused" not in before, before
    assert after != before, f"closure {mode}: an edit to {BUILDER} reused the step's verdict"


def test_every_key_mode_keys_one_list_of_parts() -> None:
    """The whole-content key and the step closure read the same parts: goh's code parts
    (run_rule.CODE_PARTS, the parts a declared hole's tail may name) and the binary's build
    inputs, never a copy of either."""
    code = len(run_rule.CODE_PARTS)
    assert gate_identity.PARTS[:code] == run_rule.CODE_PARTS, gate_identity.PARTS
    assert not set(gate_identity.PARTS[code:]) & set(run_rule.CODE_PARTS)
    assert not hasattr(sc, "STEP_PARTS"), "a second list of the step's parts"


def _runs(rel: str) -> bool:
    return rel.endswith(source_closure.CODE) or source_closure._is_shell(REPO_ROOT / rel)


# Two of goh's own GOH_CI_STEPS (.gatesrc), as tests/test_step_closure_refuse.py walks them.
GOH_STEPS = ("./gates/rust_gate.sh", "./gates/structural.sh --full")


# Code files the closures reach outside PARTS that no step runs: a name, never a run (`file` ->
# who names it and why). A ratchet: an entry no closure reaches any more fails.
MENTIONS = {
    "install.sh": "gates/doctor.sh and gates/_goh_bin.sh name it in a fix message, never run it",
    ".githooks/pre-push": "lib/gate_coverage.py ENTRIES: the gated repo's hook, read from it",
}


def test_no_code_file_a_real_step_runs_is_outside_the_default_key() -> None:
    """The real tree: every code file the runner's closure and two of goh's own steps reach is
    under the parts the default (whole-content) key reads, or is a name MENTIONS says no step
    runs. 2026-11-19, before GOH.FULLKEYSCRIPTS: scripts/build-goh.sh, run, was outside it."""
    _fresh()
    reach = set(sc.runner_closure(REPO_ROOT))
    for step in GOH_STEPS:
        gohs, _, _ = sc._step_seeds(step, REPO_ROOT, REPO_ROOT)
        reach |= sc._goh_reach(gohs, REPO_ROOT, judge=False)[0]
    keyed = set(gate_identity._files(REPO_ROOT, gate_identity.PARTS, data=True))  # stock hooks too
    outside = {r for r in reach if _runs(r) and r not in keyed}
    omitted = sorted(outside - set(MENTIONS))
    assert not omitted, f"files a step runs that its default key omits: {omitted}"
    assert not sorted(set(MENTIONS) - outside), "MENTIONS no closure reaches any more"


# The four `source` lines of the runner's closure no reading places, each declared safe.
# `<file>: . <word>` -> the run_rule reason (by name) that says why.
RUNNER_SOURCES = {
    "gates/local_ci.sh: . $ROOT/.gatesrc": "GATESRC",
    "gates/proven.sh: . $top/.gatesrc": "GATESRC",
    "gates/local_ci.sh: . $_tui": "TUI",
    "gates/_from_head.sh: . $_goh_copy": "HEAD_COPY",
}


def test_each_unplaced_source_of_the_runner_is_declared_with_why() -> None:
    """The real tree: the runner's closure meets exactly these source holes, each declared."""
    _fresh()
    met = set()
    for rel in sc.runner_closure(REPO_ROOT):
        if source_closure._is_shell(REPO_ROOT / rel):
            met.update(run_rule.source_holes(rel, REPO_ROOT))
    assert met == set(RUNNER_SOURCES), sorted(met)
    for form, why in RUNNER_SOURCES.items():
        assert run_rule.DECLARED.get(form) == getattr(run_rule, why), form


def test_an_undeclared_source_refuses_the_step_its_key(tmp_path) -> None:
    goh = _goh(tmp_path / "goh", '#!/usr/bin/env bash\n. "$ELSEWHERE"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    _fresh()
    with pytest.raises(LookupError, match=r"gates/local_ci\.sh: \. \$ELSEWHERE"):
        sc.step_closure(PLACED, repo, goh)
    assert _key("on", goh, repo).startswith("closure refused")


def test_a_declared_source_keeps_the_step_its_key(tmp_path) -> None:
    """The runner's own `. "$ROOT/.gatesrc"` is the gated repo's: declared, so no refusal."""
    goh = _goh(tmp_path / "goh", '#!/usr/bin/env bash\nROOT="$PWD"\n. "$ROOT/.gatesrc"\n')
    repo = tmp_path / "repo"
    repo.mkdir()
    _fresh()
    files, why = sc.step_closure(PLACED, repo, goh)
    assert "gates/local_ci.sh" in files and not why, files


def test_every_declared_source_is_still_in_the_tree() -> None:
    """A ratchet: a declared `. <word>` its file no longer holds as a source hole fails."""
    gone = [e for e in run_rule.DECLARED if ": . " in e
            and e not in run_rule.source_holes(e.split(": ", 1)[0], REPO_ROOT)]  # fmt: skip
    assert not gone, "DECLARED source holes the tree no longer has: " + "; ".join(gone)


# Source holes of goh's code no step's key may follow: refused (a step reaching one runs).
REFUSED = {
    # A test script that cds to its own directory first; no gate step runs it.
    "lib/desktop_lock/test_desktop_lock.sh: . desktop_lock.sh",
    "lib/desktop_lock/test_desktop_lock.sh: . ../tmp_root.sh",
    "lib/desktop_lock/test_desktop_lock.sh: . ../pid_owner.sh",
}


def test_every_source_hole_of_goh_is_declared_or_pinned_refused() -> None:
    """The real tree, every shell file of goh's code: a new unplaced `source` either says why it
    is safe (run_rule.DECLARED) or fails here first, never silently in every step reaching it."""
    _fresh()
    holes = set()
    for rel in source_closure.tree_files(REPO_ROOT):
        if rel.split("/")[0] in run_rule.CODE_PARTS and source_closure._is_shell(REPO_ROOT / rel):
            holes.update(run_rule.source_holes(rel, REPO_ROOT))
    refused = holes - set(run_rule.DECLARED)
    assert refused == REFUSED, sorted(refused ^ REFUSED)


def test_a_keyword_needs_its_blank_before_a_source(tmp_path) -> None:
    """`else. Build it` in a message (gates/structural.sh) is no `source Build`."""
    text = 'err "run something else. Build it: x"\nif a; then . "$X"; fi\n'
    _write(tmp_path, {"gates/g.sh": text})
    assert run_rule.source_holes("gates/g.sh", tmp_path) == ["gates/g.sh: . $X"]


# goh's own GOH_COV_RUST_EXTERNAL (.gatesrc): the suite's runner the coverage identity reads.
EXTERNAL = 'GOH_TEST_BIN="$GOH_COVERAGE_BIN_DIR/goh" ./tools/pytest.sh'


def test_the_coverage_identity_places_a_file_root_when_goh_dir_is_another_tree(tmp_path) -> None:
    """A land's coverage identity reads the repo (its worktree) with GOH_DIR another tree (an
    export): gates/_goh_bin.sh's `$here` (run_rule.FILE_ROOTS) was placed only for a file under
    GOH_DIR, so the runner's `$here/../scripts/build-goh.sh` was a hole there, the identity was
    refused, and every land measured coverage again (lander.log, 2026-11-19)."""
    other = tmp_path / "goh"  # goh's coverage scripts at another path, as a land's export
    source_closure.copy_closure(
        other, *gate_identity.rust_gate_scripts(REPO_ROOT), "lib/timing_ledger.py", root=REPO_ROOT
    )
    runner = rust_cov_scripts.runner_files(REPO_ROOT, EXTERNAL, other)
    assert BUILDER in runner, runner
    lines = gate_identity.rust(REPO_ROOT, other, EXTERNAL)
    assert any(ln.startswith("runner ") and BUILDER in ln for ln in lines)
