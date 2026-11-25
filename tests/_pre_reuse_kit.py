"""The pre-step reuse kit: a throwaway repo with a stand-in reads table and the pre-step's own code
in small, and the histories and lands tests/test_pre_reuse*.py judge (GOH.PRELANDTWICE).

Lifted out of tests/test_pre_reuse.py (GOH.PUSHGATEUNBLOCK): that module named gates/push_gate.sh
as a fixture's file name, so every test importing these helpers counted as a push_gate.sh reader
and the real-tree pin (tests/test_affected_test_imports.py) sat at 100. The stand-in gate here is
`gates/never_run.sh`, a gate the pre-step never runs; no file of the real tree is named.
"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import REPO_ROOT, git
from test_landq import _land_wt, landq, recorded

READS = """\
import sys
sys.path.insert(0, {kit!r})
from lib.test_closure import Closure  # the kit's graph: what the pre-step's code reaches
from lib import binary_runners  # the binary's build inputs: what `_built` asks the table
del sys.path[0]
try:
    import sel_helper  # noqa: F401 -- the selection's own lib/ module, when the tree has one
except ImportError:
    pass
READ = {{"read.txt": "tests/test_r.py", "gates/never_run.sh": "tests/test_push.py",
        "checks/cad_bambu.py": "tests/test_cad.py", "lib/sel_helper.py": "tests/test_sel.py",
        "Cargo.lock": "tests/test_bin.py", "crates/goh/src/x.rs": "tests/test_bin.py"}}
def readers(root, changed, tests=None):
    return {{READ[p]: "reads " + p for p in changed if p in READ}}
def affected(root, changed):  # THE SET: a tree reader's node, the readers, what sel_helper names
    import pathlib, re
    got = {{"tests/test_walk.py::test_every_x": "a tree reader", **readers(root, changed)}}
    sel = pathlib.Path(root, "lib", "sel_helper.py")
    for name in re.findall(r"tests/test_[a-z_]+[.]py", sel.read_text() if sel.is_file() else ""):
        got[name] = "lib/sel_helper.py chooses it"
    return dict(sorted(got.items()))
if __name__ == "__main__":
    with open({seen!r}, "a") as f:
        s = "+structural " if "--structural" in sys.argv else ""
        f.write("narrow " + s + " ".join(sys.argv[sys.argv.index("--changed") + 1 :]) + "\\n")
"""


# The pre-step's own code in small (GOH.PRESTEPMOVED): the structural gates source _common.sh and
# the binary runs checks/check_*.py; the selection imports lib/sel_helper.py. never_run.sh and the
# CAD scanner are gates and checks the pre-step never runs.
KIT_TREE = {
    "gates/structural.sh": '. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"\n',
    "gates/_common.sh": "x=1\n",
    "gates/never_run.sh": "echo push\n",
    "checks/check_x.py": "print(1)\n",
    "checks/cad_bambu.py": "print(2)\n",
    "lib/sel_helper.py": "X = 1\n",
    "tools/pytest.sh": "exit 0\n",
}


def _with_table(repo: Path, tmp_path: Path, kit: bool = False) -> Path:
    seen = tmp_path / "seen"
    (repo / "tools").mkdir(exist_ok=True)
    (repo / "tools" / "affected_tests.py").write_text(
        READS.format(seen=str(seen), kit=str(REPO_ROOT))
    )
    for name in ("read.txt", "CHANGELOG.md", "Cargo.lock"):
        (repo / name).write_text("main's first\n")
    for name, text in KIT_TREE.items() if kit else ():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "a reads table")
    return seen


def _main_moves(repo: Path, name: str) -> None:
    (repo / name).write_text("main moved it\n")
    git(repo, "commit", "-q", "--no-verify", "-am", f"main moves {name}")


def _land(
    repo: Path,
    tmp_path: Path,
    env: dict,
    moves: str | None,
    keep: bool = True,
    kit: bool = False,
    record: str = "",
):
    seen = _with_table(repo, tmp_path, kit)
    wt, _ = _land_wt(repo, tmp_path)
    pre = f"echo full >> {seen}" + (
        f' && cp {record} "$GOH_LAND_VERDICTS_RECORD"' if record else ""
    )
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.T", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    if moves:
        _main_moves(repo, moves)
    if not keep:
        for kept in (tmp_path / "landq" / "pre").iterdir():
            kept.unlink()
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.T"]
    assert got["state"] == "landed", Path(got["log"]).read_text()
    return seen.read_text().splitlines(), Path(got["log"]).read_text()


# The decision itself, on a two-commit history: what moved between the judged tip and the new one.
def _history(
    repo: Path,
    tmp_path: Path,
    judged_adds: str,
    main_moves: str,
    kit: bool = False,
    main_text: str = "main's\n",
) -> tuple[str, ...]:
    _with_table(repo, tmp_path, kit)
    base0 = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "lane")
    (repo / judged_adds).parent.mkdir(parents=True, exist_ok=True)
    (repo / judged_adds).write_text("the lane's\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "lane")
    judged = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", base0)
    (repo / main_moves).parent.mkdir(parents=True, exist_ok=True)
    (repo / main_moves).write_text(main_text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "main")
    base = git(repo, "rev-parse", "HEAD").strip()
    # What `git rebase` of the lane's one commit onto main makes, made as a commit: its 20 files of
    # state are git's own subject (GOH.FSEVQUIET).
    git(repo, "checkout", "-q", "-B", "lane")
    (repo / judged_adds).parent.mkdir(parents=True, exist_ok=True)
    (repo / judged_adds).write_text("the lane's\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "lane")
    return judged, git(repo, "rev-parse", "HEAD").strip(), base


JUDGED_SET = ["tests/test_walk.py::test_every_x"]


def _judged_set(tmp_path: Path, chosen: list[str] | None) -> Path:
    record = tmp_path / "judged.json"
    rec = {"identity": "i", "sha": "s", "passed": {}, "selection": "affected", "base": "b" * 40}
    record.write_text(json.dumps({**rec, "chosen": chosen} if chosen is not None else rec))
    return record
