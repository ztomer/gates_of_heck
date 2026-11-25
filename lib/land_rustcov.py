"""lib/land_rustcov.py -- a land measures the diff coverage of its own changed Rust lines.

GOH.LANDRUSTCOV. Since GOH.COVOFFLAND only the ship's instrumented full run measured Rust coverage,
and since GOH.DIFFCOVUNMEASURED a new Rust line was "unmeasured" at the land (lib/cov_risk.py), so
every Rust change was first judged by a ship: two v0.145.0 attempts (2026-11-22, 01:21 and 02:22)
went red at diff coverage after an 8-14 min suite on 2-3 new lines each
(structural/only.rs:108-110, toolshell/observed.rs:64,112), each red a fix land and a full re-ship.

THE RUN IS THE PRE-STEP'S OWN. A Rust change chooses most of the binary's tests (239 entries for
GOH.SHELLCHECKREAL's diff): measuring them in a run of their own was the pre-step again, 516 s and
unfinished at load ~120 on top of 264 s of cold builds. So the pre-step's second run
(tools/affected_tests.py --run --rust-cov, goh's GOH_LAND_PRE), into which the chosen tests that
run the binary move from the judges' run (`selected`: only those can cover a Rust line), runs
under the coverage gate's Rust mode -- the machinery the ship's run uses (lib/ship_full_run.py,
gates/coverage_gate.sh) -- as a SUBSET run, `--diff-only` (`argv`): the instrumented build, the
workspace's tests, and the tree's GOH_COV_RUST_EXTERNAL over the run's tests with GOH_TEST_BIN the
instrumented goh. Its verdict record is written as the plain run's was. gates/diff_cov.py then
judges every changed executable Rust line since the land's merge base with main
(GOH_CHANGE_BASE): an uncovered one turns the pre-step, so the land, red, named `file:line`. The
selection is sound for this: a test it leaves out cannot reach the changed code
(lib/binary_runners.py, lib/crate_reach.py), so an uncovered line here is one the ship's full run
leaves uncovered too. Red at the land, not carried to the ship: a carry would refuse the ship
before its suite but still cost a fix land after the queue; red here costs the lane one commit.

NOT RUN (`plan` returns None, said) for a change touching no `.rs` file, a tree that declares no
external suite, or inside a running goh suite. No floor and no coverage claim (--diff-only).
Pinned by tests/test_land_rustcov.py.

A RETRY CARRIES (GOH.RUSTPRECARRY). A red on one node cost the whole set again: of 47 enqueue
pre-steps of a Rust change in 7 days, 11 retried a measured run on an unchanged binary (11,429 s;
OLDFORMS 1050 s then 1054 s). The measured run now carries as the plain one does
(lib/affected_verdict.py `carrying`): tests/_land_verdicts.py skips a test that passed on another
tree, that the diff between the trees does not choose and that read none of it; one that ran the
binary only when its stored profile is of these instrumented bytes, which the report then counts
(GOH.COVCARRY3, the land gate's rule, audited as `carry`). The tree carried from is named: `kin`,
the nearest one measured with the same build inputs, beside the record (`named`). Named because 8
of the 12 retries were rebased, so the tree they had failed on was no ancestor of theirs.

THE WORKER'S SHARE. A chosen test that does not run the binary covers no Rust line: on a first run
the Linux-safe ones go to the remote worker beside it (`split`; 14% of the set's seconds over four
logs). Not on a retry, which carries them in seconds.

GOH.LANDRUSTCOV: a land measures the diff coverage of its own changed Rust lines. When the land
changes a `.rs` file, the pre-step's second run (`tools/affected_tests.py --run --rust-cov`, goh's
`GOH_LAND_PRE`; the chosen tests that run the binary moved into it from the judges' run,
`selected`) runs under `gates/coverage_gate.sh --lang rust --diff-only` (`argv`: the ship's
coverage machinery as a SUBSET run, no floor, no claim): the instrumented build, the workspace's
tests, then `GOH_COV_RUST_EXTERNAL` over those tests against the instrumented goh, its verdict
record kept; `gates/diff_cov.py` judges every changed Rust line since the merge base with main, red
naming `file:line`. Replaces the plain run of the same tests, so the added wall is the builds and
the workspace's tests. Not run when no `.rs` file changed (`plan`). A retry carries
(GOH.RUSTPRECARRY): a test that passed on the nearest tree measured with the same build inputs
(`kin`, the profile store's ledger) is not run again unless the diff between them reaches it, its
stored profile counted; a first run sends the worker the chosen tests that run no binary (`split`).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib")]
from lib import binary_runners, gitrun, prof_store, test_closure  # noqa: E402
from tui.lib import counted, info  # noqa: E402

KIN = ".kin"  # beside the record the measured run writes: the tree it carries from (`named`)
# A part of the suite, or of its coverage, the caller's environment names: never this run's.
PARTS = ("GOH_COV_PART", "GOH_COV_PARTS_IN")
DROP = ("GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE")  # `judge` alone: no land's record


def _git(root: Path, *args: str) -> str:
    r = gitrun.run_reader(list(args), repo=str(root), timeout=120)
    return r.stdout.strip() if r.returncode == 0 else ""


def rust_changed(root: Path, base: str) -> list[str]:
    """The `.rs` files HEAD changes against its merge base with BASE (deleted ones are no line)."""
    out = _git(root, "diff", "--name-only", "--diff-filter=d", f"{base}...HEAD", "--", "*.rs")
    return [p for p in out.splitlines() if p]


def external(root: Path) -> str:
    """The tree's GOH_COV_RUST_EXTERNAL, read from its .gatesrc in a child as the gates read it."""
    if not (root / ".gatesrc").is_file():
        return ""
    read = (
        "unset GOH_COV_RUST_EXTERNAL; . ./.gatesrc >/dev/null 2>&1 </dev/null; "
        'printf %s "${GOH_COV_RUST_EXTERNAL-}"'
    )
    out = subprocess.run(["bash", "-c", read], cwd=root, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
    return out.stdout


def plan(root: Path, base: str) -> tuple[str, str] | None:
    """(the merge base the change is judged from, the external command) when this change owes the
    measurement; None, said, when it does not."""
    changed = rust_changed(root, base)
    if not changed:
        info("rust diff coverage: no Rust file changed -- nothing to measure")
        return None
    cmd = external(root)
    if not cmd:
        info("rust diff coverage: the tree declares no GOH_COV_RUST_EXTERNAL -- not measured here")
        return None
    if os.environ.get("GATES_OF_HECK_SUITE_RUNNER") and not os.environ.get(
        "GATES_OF_HECK_SUITE_NESTED_OK"
    ):
        info("rust diff coverage: not run inside a running goh suite, which measures its own")
        return None
    mb = _git(root, "merge-base", "HEAD", base)
    if not mb:
        info(f"rust diff coverage: no merge base of HEAD with {base} -- not measured")
        return None
    info(f"rust diff coverage: {counted(len(changed), 'changed Rust file')} since {mb[:8]}")
    return mb, cmd


def selected(root: Path, chosen: list[str]) -> list[str]:
    """The entries of CHOSEN (test files or node ids) whose file runs the binary: the only tests
    that can cover a Rust line (lib/binary_runners.py `runners`, the selection's own reader)."""
    tests = {
        f"tests/{p.name}": p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("test_*.py"))
    }
    files = {n.split("::")[0] for n in chosen} & set(tests)
    graph = test_closure.Closure(root)
    runs = binary_runners.runners(graph, {f: tests[f] for f in files}, "crates")
    return [n for n in chosen if n.split("::")[0] in runs]


def split(root: Path, judges: list[str], rest: list[str],
          kin: str = "") -> tuple[list[str], list[str], list[str]]:  # fmt: skip
    """(the judges' run, the measured one with the judges that run the binary moved into it, the
    tests the worker may share: those that run no binary, none of a retry's, which carries)."""
    binary = set(selected(root, [*judges, *rest]))
    far = [] if kin else [n for n in rest if n not in binary]
    mine = sorted(n for n in judges if n in binary)
    return ([n for n in judges if n not in binary],
            [*mine, *(n for n in rest if kin or n in binary)], far)  # fmt: skip


def _paths(root: Path, tree: str) -> list[str] | None:
    """The paths whose bytes differ between TREE and HEAD's; None when git cannot say."""
    r = gitrun.run_reader(["diff", "--name-only", "--no-renames", tree, "HEAD"], repo=str(root),
                          timeout=120)  # fmt: skip
    return [p for p in r.stdout.splitlines() if p] if r.returncode == 0 else None


def kin(root: Path) -> str:
    """The tree this measured run carries from: of those whose tests' profiles the store still
    holds (lib/prof_store.py's ledger) and whose build inputs are HEAD's, the nearest by paths, the
    newest of those. "" for none. Only a name: each node's own verdict, diff, reads and profile
    still decide (tests/_land_verdicts.py `_carry`), so a wrong one carries nothing."""
    best: tuple[int, str] | None = None
    for row in reversed(prof_store.ledger()):
        tree = str(row.get("tree", ""))
        if not prof_store.live(row) or (paths := _paths(root, tree)) is None:
            continue
        if not binary_runners.build_input(root, paths) and (best is None or len(paths) < best[0]):
            best = (len(paths), tree)
    if best is None:
        return ""
    info(f"rust diff coverage: carries from tree {best[1][:8]}, measured with the same build "
         f"inputs ({counted(best[0], 'path')} differ)")  # fmt: skip
    return best[1]


@contextlib.contextmanager
def named(record: str, tree: str) -> Iterator[None]:
    """TREE beside RECORD while its run lasts (tests/_land_trees.py `sources`); none: nothing."""
    on = Path(f"{record}{KIN}") if tree and record and record != os.devnull else None
    if on:
        on.write_text(tree, encoding="utf-8")
    try:
        yield
    finally:
        if on:
            on.unlink(missing_ok=True)


def argv(root: Path, planned: tuple[str, str], tests: list[str]) -> list[str]:
    """The coverage gate's subset run over TESTS (none: the workspace's tests alone), judged from
    the PLANNED merge base; run with the caller's environment, its land record included."""
    mb, cmd = planned
    ext = f"{cmd} -q {shlex.join(tests)}" if tests else ":"  # pytest.sh with no file is the suite
    return ["env", *(a for k in PARTS for a in ("-u", k)), f"GOH_CHANGE_BASE={mb}",
            "GOH_COV_DIFF=on",
            "bash", str(ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust", "--diff-only",
            "--external", ext, str(root)]  # fmt: skip


def judge(root: Path, base: str, tests: list[str]) -> int:
    """`argv` run alone (no land's record): 0 green or not owed, 1 an uncovered changed line
    (named by gates/diff_cov.py) or a red test, 2 a refused run."""
    planned = plan(root, base)
    if planned is None:
        return 0
    env = {k: v for k, v in os.environ.items() if k not in DROP}
    return subprocess.run(argv(root, planned, tests), cwd=root, env=env,
                          stdin=subprocess.DEVNULL, check=False).returncode  # fmt: skip
