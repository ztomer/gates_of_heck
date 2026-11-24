"""GOH.FILERSTAMP: the queue tool refuses an item filed with no ship package, and every machine
filer of goh files through ONE funnel (lib/reuse_fault._file), so the funnel asks the tool to stamp
the current open package (LOOPCTL_STAMP_PACKAGE=1 in the child's environment) and cap_ahead names
the package its split items belong to (3, the line-cap splits). A fake loopctl refuses a
package-less filing unless the variable is set."""

from __future__ import annotations

import ast
import itertools
import json
import sys
from pathlib import Path

import cap_ahead
import reuse_fault
from conftest import REPO_ROOT as ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import spend_watch

STAMP = "LOOPCTL_STAMP_PACKAGE"
STRICT = """import json, os, pathlib, sys
argv = sys.argv[1:]
if argv[:1] == ["--queue"]:
    argv = argv[2:]
assert argv[0] == "file" and argv[1] == "--json", argv
item = json.loads(argv[2])
stamped = os.environ.get("LOOPCTL_STAMP_PACKAGE", "") not in ("", "0")
if item.get("package") is None:
    if not stamped:
        print(item["id"] + ": no ship package. gates_of_heck's packages: 1 a; 2 b; 3 c")
        sys.exit(1)
    item["package"] = 1
with pathlib.Path(sys.argv[0]).with_suffix(".log").open("a") as f:
    f.write(json.dumps(item) + "\\n")
"""
ITEM = {"id": "GOH.X", "repo": "gates_of_heck", "class_of": "none", "class_why": "w"}


def strict(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("GOH_LOOP_QUEUE", raising=False)
    monkeypatch.delenv(STAMP, raising=False)
    fake = tmp_path / "loopctl.py"
    fake.write_text(STRICT)
    return fake


def filed(fake: Path) -> list[dict]:
    log = fake.with_suffix(".log")
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_the_funnel_asks_loopctl_to_stamp_the_current_package(tmp_path, monkeypatch) -> None:
    fake = strict(tmp_path, monkeypatch)
    code, said = reuse_fault._file(str(fake), ITEM)
    assert code == 0, said
    assert [i["package"] for i in filed(fake)] == [1]


def test_the_fake_refuses_a_package_less_filing_without_the_variable(tmp_path, monkeypatch) -> None:
    """The instrument can say no: a filing that does not set the variable is refused."""
    import subprocess

    fake = strict(tmp_path, monkeypatch)
    argv = [sys.executable, str(fake), "file", "--json", json.dumps(ITEM)]
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    assert r.returncode == 1 and "no ship package" in r.stdout and filed(fake) == []


def test_cap_ahead_files_its_split_items_under_package_3(tmp_path, monkeypatch) -> None:
    fake = strict(tmp_path, monkeypatch)
    fields = {**ITEM, "id": "GOH.SPLITX", "item": "Split lib/x.py: 460 lines"}
    assert cap_ahead._file_one(str(fake), fields) == "filed"
    assert [i["package"] for i in filed(fake)] == [3]


def test_the_spend_watch_files_through_the_funnel(tmp_path, monkeypatch) -> None:
    fake = strict(tmp_path, monkeypatch)
    queue = tmp_path / "queue.json"
    item = {**ITEM, "id": "SPEND.WORSE.X"}
    assert spend_watch._file(fake, queue, item) == {"id": "SPEND.WORSE.X", "outcome": "filed"}
    assert [i["package"] for i in filed(fake)] == [1]


def _argv_files(tree: ast.AST) -> bool:
    """A list or call holding the strings "file", "--json" side by side: a `loopctl file` argv."""
    for node in ast.walk(tree):
        seq = (
            node.elts
            if isinstance(node, ast.List)
            else node.args
            if isinstance(node, ast.Call)
            else []
        )
        vals = [e.value if isinstance(e, ast.Constant) else None for e in seq]
        if any(a == "file" and b == "--json" for a, b in itertools.pairwise(vals)):
            return True
    return False


def test_no_goh_code_runs_loopctl_file_outside_the_funnel() -> None:
    """Every filer's argv is reuse_fault._file's: a second spelling would file with no package."""
    own: list[str] = []
    for top in ("lib", "scripts", "hooks", "tools", "gates", "checks", "bin"):
        for path in sorted((ROOT / top).rglob("*.py")):
            if _argv_files(ast.parse(path.read_text(encoding="utf-8"))):
                own.append(str(path.relative_to(ROOT)))
    assert own == ["lib/reuse_fault.py"], own
