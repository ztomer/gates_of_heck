"""The enqueue's pre-step judges the tip REBASED onto main in its slot (GOH.PREONTIP).

The gate missed reuse in 9 of the last 10 lands of 2026-11-22, mostly "the pre-step judged another
tree or toolchain": tools/land.sh skipped its rebase under GOH_LAND_PRE_ONLY, so the enqueue judged
the lane's tip J on the base it was cut from, while the lander gates T = main + J's own commits.
Every commit main gained since the cut was a difference between the judged tree and T, so the
lander's pre-land re-ran their readers and the gate's suite (lib/pre_reuse.py `suite`) never held.

Now the enqueue rebases in the request's slot (goh's detached checkout, lib/land_slot.py; never the
lane's tree), judges P = main-now + D, pins P (`refs/goh/pre/<keep>`) and keeps (P, base). The
lander still checks the slot out at the enqueued sha and rebases it again: when main has not moved
since the enqueue, T's tree is P's, so the pre-land and the gate both reuse; when it has, only the
readers of what main moved since are judged again. A batch's gate carries from EVERY member's kept
record (`<record>.sources`, tests/_land_verdicts.py `_carry`), not the tip's alone.

The fixture's reads table: `read.txt` is read by tests/test_r.py, `x.txt` by tests/test_x.py,
`b.txt` by tests/test_b.py. The pre-step writes a record as the real suite does (identity, sha,
passed, stamped with its base); the gate asks lib/pre_reuse.py `--suite` as tools/pytest.sh does.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _reads_kit import seen
from conftest import REPO_ROOT, git, hermetic_env
from env_probe import print_safe
from test_landq import env, landq, recorded  # noqa: F401  -- `env` is the queue's fixture
from test_landq_batch import lane
from test_verdict_carry import SESSION_CONFTEST
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import _goh_db_fixture as gdb
import gate_identity
import reuse

TABLE = """\
class Closure:
    def __init__(self, root): pass
    def base(self): return set()
READ = {"read.txt": "tests/test_r.py", "x.txt": "tests/test_x.py", "b.txt": "tests/test_b.py"}
def readers(root, changed, tests=None):
    return {READ[p]: "reads " + p for p in changed if p in READ}
def affected(root, changed, enqueue=False):
    return readers(root, changed)
if __name__ == "__main__":
    import sys
    with open(%(seen)r, "a") as f:
        f.write("narrow " + " ".join(sorted(sys.argv[sys.argv.index("--changed") + 1 :])) + "\\n")
"""
NODES = {f"tests/test_{n}.py::test_{n}": False for n in ("a", "b", "r", "x")}
PRESTEP = """\
import json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, %(lib)r)
import gate_identity, pre_reuse
with open(%(seen)r, "a") as f:
    f.write("full\\n")
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
with open(%(judged)r, "a") as f:
    f.write(head + "\\n")
changed = subprocess.run(["git", "diff", "--name-only", sys.argv[1], "HEAD"], capture_output=True,
                         text=True).stdout.split()
nodes = {"tests/test_" + p.split(".")[0] + ".py::test_" + p.split(".")[0]: False for p in changed}
rec = os.environ["GOH_LAND_VERDICTS_RECORD"]
Path(rec).write_text(json.dumps({"identity": gate_identity.suite(Path.cwd()), "sha": head,
                                 "passed": nodes}))
pre_reuse.stamp(rec, sys.argv[1], sorted(nodes))
"""
# The gate's suite as tools/pytest.sh asks it, its answer kept where land.sh reads it; the sources
# a batch names are copied out for the test to read.
GATE = (
    'r="$(%(py)s %(kit)s/lib/pre_reuse.py --root . --suite "$GOH_LAND_VERDICTS_REUSE" '
    '--base "$(cat "$GOH_LAND_VERDICTS_REUSE.base")")" '
    """&& printf '%%s\\n' "${r#*$'\\n'}" >"$GOH_LAND_VERDICTS_REUSE.reused"; """
    """printf '%%s\\n' "${r%%%%$'\\n'*}" >>%(why)s; """
    'cat "$GOH_LAND_VERDICTS_REUSE.sources" >>%(sources)s 2>/dev/null; true'
)


def _commit(repo: Path, msg: str, **files: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", msg)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def kit(repo: Path, tmp_path: Path, env: dict, monkeypatch) -> dict:  # noqa: F811
    """The repo's reads table and test files; the queue's env with the fixture pre-step and gate."""
    files = {"tools/affected_tests.py": TABLE % {"seen": str(tmp_path / "seen")},
             "read.txt": "1\n", "x.txt": "1\n", "b.txt": "1\n",
             **{n.split("::")[0]: "" for n in NODES}}  # fmt: skip
    _commit(repo, "a reads table", **files)
    script = tmp_path / "prestep.py"
    script.write_text(PRESTEP % {"lib": str(REPO_ROOT / "lib"), "seen": str(tmp_path / "seen"),
                                 "judged": str(tmp_path / "judged")})  # fmt: skip
    gate = GATE % {"py": sys.executable, "kit": str(REPO_ROOT), "why": tmp_path / "why",
                   "sources": tmp_path / "sources"}  # fmt: skip
    store = tmp_path / "store"
    monkeypatch.setenv("GOH_REUSE_DIR", str(store))
    for k in ("GOH_COVERAGE_BIN_DIR", "LLVM_PROFILE_FILE"):
        monkeypatch.delenv(k, raising=False)
    return print_safe({**env, "GOH_LAND_GATE": gate, "GOH_REUSE_DIR": str(store),
                       "GOH_LAND_PRE": f'{sys.executable} {script} "$1"',
                       "GOH_TIMING_DIR": str(tmp_path / "timing")})  # fmt: skip


def _main_record(repo: Path) -> None:
    """Main's tree passed every test, in the store: the invariant every land's gate keeps."""
    tree = git(repo, "rev-parse", "HEAD^{tree}").strip()
    reuse.record_suite(repo, tree, gate_identity.suite_tools(), NODES, set(), True, "land gate",
                       reads=seen(NODES))  # fmt: skip


def _enqueue(env: dict, wt: Path, item: str) -> None:  # noqa: F811
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", item, "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr


def _land_row(tmp_path: Path) -> dict:
    rows = gdb.timed(tmp_path / "goh.db", "land")
    assert len(rows) == 1, rows
    return rows[0]


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.is_file() else []


def test_a_land_whose_main_did_not_move_since_its_enqueue_reuses_in_pre_land_and_gate(
    repo: Path, tmp_path: Path, kit: dict
) -> None:
    """The lane was cut before main moved x.txt (a file a test reads); main did not move again
    after the enqueue. The pre-step judged the rebased tip, so both reuse."""
    wt = lane(repo, tmp_path, "lane", {"b.txt": "the lane's\n"})
    _commit(repo, "main moves x.txt", **{"x.txt": "main moved it\n"})
    _main_record(repo)
    _enqueue(kit, wt, "T.T")
    assert landq(kit, "--drain").returncode == 0
    got = recorded(kit)["T.T"]
    log = Path(got["log"]).read_text()
    assert got["state"] == "landed", log
    row = _land_row(tmp_path)
    assert _lines(tmp_path / "seen") == ["full"], log  # the enqueue's run alone
    assert row.get("pre_why", "").startswith("reuse: the tree judged at"), (row, log)
    assert row.get("suite_reused"), (row, _lines(tmp_path / "why"), log)
    assert git(repo, "rev-parse", f"{got['sha']}^{{tree}}") == git(
        repo, "rev-parse", f"{_lines(tmp_path / 'judged')[0]}^{{tree}}"
    ), log  # the tree judged at enqueue is the tree that landed
    assert git(wt, "rev-parse", "HEAD").strip() == got["enqueued"], "the lane's tree moved"
    assert not git(repo, "for-each-ref", "refs/goh/pre/").strip(), "a pin outlived its land"


def test_a_land_whose_main_moved_after_its_enqueue_rejudges_only_the_readers_of_that_move(
    repo: Path, tmp_path: Path, kit: dict
) -> None:
    """Main moved x.txt before the enqueue and read.txt after it: only read.txt's readers are
    judged again (the judged tree already held main's x.txt) -- by the gate's one suite, which
    carries the rest; the lander's NARROW is decided and logged, never run (GOH.LANDONERUN)."""
    wt = lane(repo, tmp_path, "lane", {"b.txt": "the lane's\n"})
    _commit(repo, "main moves x.txt", **{"x.txt": "main moved it\n"})
    _enqueue(kit, wt, "T.T")
    _commit(repo, "main moves read.txt", **{"read.txt": "main moved it\n"})
    assert landq(kit, "--drain").returncode == 0
    got = recorded(kit)["T.T"]
    log = Path(got["log"]).read_text()
    assert got["state"] == "landed", log
    assert _lines(tmp_path / "seen") == ["full"], log  # the enqueue's run; the gate's is its own
    assert _land_row(tmp_path).get("pre_why", "").startswith("narrow: 1 moved path,"), log


def test_a_batch_gate_is_handed_every_members_kept_verdict(
    repo: Path, tmp_path: Path, kit: dict
) -> None:
    """Two lanes, one stacked gate: the gate's suite is named the lower item's judged commit as a
    source to carry from (`<record>.sources`), beside the tip's own record."""
    one = lane(repo, tmp_path, "one", {"read.txt": "one\n"})
    two = lane(repo, tmp_path, "two", {"b.txt": "two\n"})
    _commit(repo, "main moves x.txt", **{"x.txt": "main moved it\n"})
    _enqueue(kit, one, "T.ONE")
    _enqueue(kit, two, "T.TWO")
    assert landq(kit, "--drain").returncode == 0
    got = recorded(kit)
    logs = "".join(Path(v["log"]).read_text() for v in got.values())
    assert {k: v["state"] for k, v in got.items()} == {"T.ONE": "landed", "T.TWO": "landed"}, logs
    assert got["T.ONE"]["batch"]["gates"] == 1, got
    judged = _lines(tmp_path / "judged")
    assert _lines(tmp_path / "sources") == judged[:1], (judged, logs)
    assert not git(repo, "for-each-ref", "refs/goh/pre/").strip(), "a pin outlived its land"


def test_a_lane_that_conflicts_with_main_is_red_at_enqueue_naming_the_conflict(
    repo: Path, tmp_path: Path, kit: dict
) -> None:
    wt = lane(repo, tmp_path, "lane", {"b.txt": "the lane's\n"})
    _commit(repo, "main moves b.txt", **{"b.txt": "main's\n"})
    done = landq(kit, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.T",
                 "--no-start")  # fmt: skip
    out = done.stdout + done.stderr
    assert done.returncode != 0, out
    assert "conflicts with main" in out.split("nothing enqueued")[0].splitlines()[-1], out
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "lane", out
    assert _lines(tmp_path / "seen") == [], out  # the rebase refused before any test ran


# -- the gate's suite carries from every source a batch names (a fixture session) -----------------

SOURCES_CONFTEST = SESSION_CONFTEST.replace(
    'lv.trees = lambda root, pre: [("c0" * 20, "T0")]',
    'lv.trees = lambda root, pre: [("c0" * 20, "T0")]\n'
    'lv.named = lambda root, shas: [(s, "T5") for s in shas]',
)
SOURCE_TESTS = "def test_s():\n    pass\n\ndef test_t():\n    pass\n"


def test_the_gates_suite_carries_from_each_source_beside_its_record(tmp_path: Path) -> None:
    store = tmp_path / "store"
    d = tmp_path / "s"
    d.mkdir()
    assert SOURCES_CONFTEST != SESSION_CONFTEST
    (d / "conftest.py").write_text(SOURCES_CONFTEST)
    (d / "test_u.py").write_text(SOURCE_TESTS)
    rec = d / "rec.json"
    Path(f"{rec}.sources").write_text("c5" * 20 + "\n")
    run_env = hermetic_env(drop_git=True, GOH_REUSE_DIR=str(store), GOH_LOOPCTL=str(d / "none"),
                           GOH_LAND_VERDICTS_REUSE=str(rec))  # fmt: skip
    probe = (
        "import os, sys; sys.path.insert(0, sys.argv[1]); import reuse; "
        "reuse.record_suite(sys.argv[2], 'T5', 'TOOLS', "
        "dict.fromkeys(nodes := ['test_u.py::test_s', 'test_u.py::test_t'], False), set(), True, "
        "'pre-land', reads=[(nodes, {'test_u.py': []})])"
    )
    subprocess.run([sys.executable, "-c", probe, str(REPO_ROOT / "lib"), str(REPO_ROOT)],
                   env=run_env, check=True, stdin=subprocess.DEVNULL,
                   timeout=patience(60))  # fmt: skip
    r = subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA"], cwd=d,
                       env={**run_env, "CHOSEN": "test_u.py::test_s"}, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, check=False,
                       timeout=patience(120))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"1 deselected, carried from {'c5' * 4}" in r.stdout, r.stdout
    assert "PASSED test_u.py::test_s" in r.stdout and "test_u.py::test_t" not in r.stdout, r.stdout


def test_a_kept_verdict_on_the_rebased_tip_is_adopted_with_its_base(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import land_prestep

    keep = tmp_path / "keep"
    p, b, j = "a" * 40, "b" * 40, "c" * 40
    keep.write_text(f"{p}\n{b}\n")
    req = {"sha": j, "slot": "tok"}
    land_prestep.adopt(req, keep)
    assert req["pre"] == {"judged": p, "base": b, "keep": str(keep)}, req
    lone = {"sha": j}  # no slot: the lane's own tree, so the keep must name the enqueued sha
    land_prestep.adopt(lone, keep)
    assert "pre" not in lone
    keep.write_text(f"{j}\n")
    land_prestep.adopt(lone, keep)
    assert lone["pre"] == {"judged": j, "keep": str(keep)}
    assert json.dumps(req)  # the request stays one JSON object
