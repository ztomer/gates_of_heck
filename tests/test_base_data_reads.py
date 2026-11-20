"""What pytest loads before every test holds the DATA its code opens; a base file keeps its readers.

GOH.BASEDATAREADS. lib/test_closure.py's `base()` counted only code: a broken lib/lock_order.tsv,
which lib/lock_order.py opens for every share token a pooled xdist worker takes, crashed xdist for
every test (INTERNALERROR KeyError WorkerController, GOH.SELECTTESTIMPORTS' lane), while a change to
it chose only its declared readers and carried every other verdict across it. Here: each data file
the base opens (a `load-read` comment on the line opening it, lib/test_closure.py LOAD_READ) is
in the base, so a change chooses every test, a
main move reruns the pre-step and the gate carries nothing across it; the declaration is checked
against what a real pooled, sharded load opens (an audit hook on every `open`), both ways.

And (GOH.PRESTEPSELFMOVE's lane): a lane changing lib/binary_runners.py did not choose
tests/test_affected_binary*.py, which reach it through base files (tests/_land_verdicts.py,
tools/affected_tests.py) the closure skipped. A base file a test imports itself, not through
tests/conftest.py, brings its imports within the base (lib/test_closure.py `base_reach`); its names
and runs stay every test's, which no test is chosen by.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _land_verdicts as lv
import pytest
from conftest import REPO_ROOT, hermetic_env
from test_pre_reuse import _history, pre_reuse
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import source_closure, test_closure

LOCKS = {
    "tests/conftest.py": "import lock_order\n",
    "lib/lock_order.py": (
        "from pathlib import Path\n"
        'TABLE = Path(__file__).resolve().with_name("lock_order.tsv")'
        "  # load-read: lib/lock_order.tsv\n"
        "def table():\n    return TABLE.read_text()\n"
    ),
    "lib/lock_order.tsv": "load\t1\n",
    "tests/test_a.py": "from conftest import X\n",
    "tests/test_lock.py": 'TSV = "lib/lock_order.tsv"\n',
}


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


@pytest.mark.parametrize("loaded", [True, False])
def test_a_data_file_the_base_opens_is_in_the_base_and_chooses_every_test(tmp_path, loaded):
    """Loaded by conftest's import of lib/lock_order.py, the table is read before every test: a
    change to it chooses each test file whole. A tree whose conftest loads no lib/lock_order.py
    reads it only where a test says so."""
    files = {**LOCKS, "tests/conftest.py": LOCKS["tests/conftest.py"] if loaded else ""}
    root = _tree(tmp_path, files)
    assert ("lib/lock_order.tsv" in test_closure.Closure(root).base()) is loaded
    chosen = at.affected(root, ["lib/lock_order.tsv"])
    want = ["tests/test_a.py", "tests/test_lock.py"] if loaded else ["tests/test_lock.py"]
    assert list(chosen) == want, chosen
    if loaded:
        assert "lib/lock_order.tsv" in chosen["tests/test_a.py"], chosen


def test_the_gate_carries_nothing_across_a_base_data_file(tmp_path):
    root = _tree(tmp_path, LOCKS)
    got, why = lv.choose(root, ["lib/lock_order.tsv"], at)
    assert got is None and "lib/lock_order.tsv is loaded before every test" in why, (got, why)


def test_a_main_move_of_a_base_data_file_reruns_the_pre_step(repo, tmp_path):
    _tree(repo, {k: v for k, v in LOCKS.items() if not k.startswith("tests/test_")})
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/lock_order.tsv")
    code, why, _ = pre_reuse.decide(repo, judged, tip, base)
    assert code == pre_reuse.RERUN, why
    assert "lib/lock_order.tsv moved: pytest loads it before every test" in why, why


def test_each_declared_data_read_is_a_base_file_line_that_names_it():
    """The declaration sits on the code that opens it: every `load-read` comment in the tree is in
    a file of the real base, names a file that exists, and its line names that file, whole or as a
    sibling's quoted name (`with_name("lock_order.tsv")`)."""
    graph = test_closure.Closure(REPO_ROOT)
    base, declared = graph.base(), graph.load_reads()
    assert {"lib/lock_order.tsv", "tests/shard_weights.json", "tui/stylerc"} <= set(declared)
    for rel, text in graph.texts.items():
        for m in test_closure.LOAD_READ.finditer(text):
            data, line, name = m.group(1), m.group(0), Path(m.group(1)).name
            assert rel in base, f"{rel}: declares {data}, but pytest does not load it"
            assert declared.get(data) == rel and data in base, f"{rel}: {data} is not in the tree"
            sibling = Path(data).parent == Path(rel).parent and (
                f'"{name}"' in line or f"'{name}'" in line
            )
            assert sibling or source_closure.names_path(line, data), f"{rel}: {line!r}"


PROBE = """\
import os, sys
ROOT, LOG = os.environ["GOH_OPEN_PROBE_ROOT"], os.environ["GOH_OPEN_PROBE_LOG"]
def _hook(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = os.path.realpath(os.fsdecode(args[0]))
        if path.startswith(ROOT + os.sep) and os.path.isfile(path) and "__pycache__" not in path:
            with open(f"{LOG}.{os.getpid()}", "a") as f:
                f.write(path[len(ROOT) + 1 :] + "\\n")
sys.addaudithook(_hook)
"""


def _opened_by_a_load(tmp_path: Path) -> set[str]:
    """The tree's files a real pytest load opens: a 2-worker xdist run of goh's own conftest under
    a share pool (every worker takes a token) and a shard (the controller deals the weights), with
    no test to run. An audit hook in every process (`-p`, which xdist hands each worker) logs it."""
    (tmp_path / "plug").mkdir()
    (tmp_path / "plug" / "goh_open_probe.py").write_text(PROBE)
    (tmp_path / "pool").mkdir()
    root = str(REPO_ROOT.resolve())
    env = hermetic_env(
        PYTHONPATH=f"{tmp_path / 'plug'}{os.pathsep}{root}",
        GOH_OPEN_PROBE_ROOT=root,
        GOH_OPEN_PROBE_LOG=str(tmp_path / "opened"),
        GOH_SHARE_POOL=str(tmp_path / "pool"),
        GOH_SHARE_CEIL="2",
        GOH_SUITE_SHARD="1/1",
    )
    for key in ("GATES_OF_HECK_SUITE_SHARE", "PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS"):
        env.pop(key, None)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "2", "-p", "goh_open_probe",
         "-p", "no:cacheprovider", "--rootdir", root, "-c", f"{root}/pyproject.toml",
         f"{root}/tests/conftest.py"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
        stdin=subprocess.DEVNULL, timeout=patience(300),
    )  # fmt: skip
    assert run.returncode == 5, run.stdout[-3000:] + run.stderr[-3000:]  # 5: no test collected
    opened = {ln for log in tmp_path.glob("opened.*") for ln in log.read_text().splitlines()}
    ignored = subprocess.run(
        ["git", "-C", root, "check-ignore", "--stdin"], input="\n".join(sorted(opened)),
        capture_output=True, text=True, check=False,
    ).stdout.split()  # fmt: skip
    return opened - set(ignored)


def test_every_data_file_a_real_load_opens_is_in_the_base_and_each_declared_one_is_opened(
    tmp_path,
):
    """The gate on LOAD_READ: what goh's load opens, read from the processes themselves, is code
    or the base (or a SUITE_WIDE file, read before any plugin), and no entry is stale."""
    graph = test_closure.Closure(REPO_ROOT)
    opened = _opened_by_a_load(tmp_path)
    assert {"lib/lock_order.tsv", "tui/stylerc"} <= opened, sorted(opened)  # the probe sees opens
    data = {p for p in opened if p not in graph.texts}
    missing = sorted(data - graph.base() - set(lv.SUITE_WIDE))
    assert not missing, f"opened while pytest loads, no `load-read` declares it: {missing}"
    stale = sorted(set(graph.load_reads()) - opened)
    assert not stale, f"declared `load-read`, never opened by a load: {stale}"


def test_a_base_module_a_test_imports_itself_is_followed(tmp_path):
    """The base a test skips is what it reaches through tests/conftest.py: a base module it imports
    itself brings that module's imports (tests/test_affected_binary_reach.py imports
    tools/affected_tests.py, which imports lib/binary_runners.py)."""
    root = _tree(
        tmp_path,
        {
            "tests/conftest.py": "import _lv\n",
            "tests/_lv.py": "import sel\n",
            "lib/sel.py": "import runners\n",
            "lib/runners.py": "",
            "tests/test_a.py": "from conftest import X\n",
            "tests/test_r.py": "from conftest import X\nimport _lv\n",
        },
    )
    assert "lib/runners.py" in test_closure.Closure(root).base()
    chosen = at.affected(root, ["lib/runners.py"])
    assert list(chosen) == ["tests/test_r.py"], chosen


def test_a_binary_runners_change_chooses_its_readers_on_the_real_tree():
    chosen = at.readers(REPO_ROOT, ["lib/binary_runners.py"])
    for rel in ("tests/test_affected_binary.py", "tests/test_affected_binary_reach.py"):
        assert rel in chosen, sorted(chosen)
