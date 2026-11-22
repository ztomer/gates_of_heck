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
external suite, or inside a running goh suite. No floor and no coverage claim (--diff-only), no
stored per-test profile (GOH_PROFCARRY_TESTS=off). Pinned by tests/test_land_rustcov.py.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lib")]
from lib import gitrun  # noqa: E402
from tui.lib import counted, info  # noqa: E402

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
    from lib import binary_runners, test_closure

    tests = {
        f"tests/{p.name}": p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((root / "tests").glob("test_*.py"))
    }
    files = {n.split("::")[0] for n in chosen} & set(tests)
    graph = test_closure.Closure(root)
    runs = binary_runners.runners(graph, {f: tests[f] for f in files}, "crates")
    return [n for n in chosen if n.split("::")[0] in runs]


def split(root: Path, judges: list[str], rest: list[str]) -> tuple[list[str], list[str]]:
    """The pre-step's two runs with the judges that run the binary moved into the measured one."""
    binary = set(selected(root, judges))
    return [n for n in judges if n not in binary], [*sorted(binary), *rest]


def argv(root: Path, planned: tuple[str, str], tests: list[str]) -> list[str]:
    """The coverage gate's subset run over TESTS (none: the workspace's tests alone), judged from
    the PLANNED merge base; run with the caller's environment, its land record included."""
    mb, cmd = planned
    ext = f"{cmd} -q {shlex.join(tests)}" if tests else ":"  # pytest.sh with no file is the suite
    return ["env", f"GOH_CHANGE_BASE={mb}", "GOH_PROFCARRY_TESTS=off", "GOH_COV_DIFF=on",
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
