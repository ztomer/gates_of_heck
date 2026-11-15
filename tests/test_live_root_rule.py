"""GOH_LIVE_ROOT is an EXPORT's: every reader honours it only for an export (GOH.LIVEROOTLEAK).

gates/_from_head.sh exports GOH_LIVE_ROOT beside the GOH_DIR it points at a HEAD export: the
live checkout the export was cut from, where its binary and history are. It is exported, so it
leaks into whatever the export's gate starts, and the reader that took it from anywhere answered
for the wrong checkout: the v0.119.0 ship's release worktree resolved main's binary through it
(cee10b5, tests/test_live_root_leak.py). That fixed the binary resolver alone; the dead-GOH_DIR
repair (lib/goh_root.sh, lib/goh_root.py, the native binary) still took GOH_LIVE_ROOT for ANY
GOH_DIR that was gone -- a removed lane worktree, a fixture's checkout -- and pointed a process at
a checkout nobody exported for it.

ONE predicate now (lib/goh_root.sh `goh_is_export`, lib/goh_root.py `is_export`,
crates/goh/src/goh_dir.rs `is_export`): a directory is an export when it holds `.goh-head`, or,
once pruned, when it sat in the head export cache (`${GOH_HEAD_CACHE:-$XDG_CACHE_HOME/goh/head}`).
This table runs every case through all three readers; they must agree.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env, native_goh_path

SHA = "c06bc794" * 5
PY_ROOT = "import sys; sys.path.insert(0, sys.argv[1]); import goh_root; print(goh_root.goh_root())"
PY_IS_EXPORT = (
    "import sys; sys.path.insert(0, sys.argv[1]); import goh_root; "
    "sys.exit(0 if goh_root.is_export(sys.argv[2]) else 1)"
)


def _goh(root: Path) -> Path:
    (root / "gates").mkdir(parents=True)
    return root


def _case(tmp_path: Path, dead: str, live: bool, default: bool) -> tuple[dict[str, str], dict]:
    cache = tmp_path / "cache" / "goh" / "head"
    home = tmp_path / "home"
    home.mkdir()
    places = {
        "export": cache / SHA,  # a pruned export: it sat in the head cache
        "worktree": tmp_path / "removed-lane",  # any other directory that is gone
    }
    names = {"default": home / "Projects" / "gates_of_heck", "live": tmp_path / "live"}
    if default:
        _goh(names["default"])
    env = hermetic_env(HOME=str(home), GOH_DIR=str(places[dead]), GOH_HEAD_CACHE=str(cache))
    if live:
        env["GOH_LIVE_ROOT"] = str(_goh(names["live"]))
    return env, {**names, **places}


CASES = {
    # (GOH_DIR that is gone, GOH_LIVE_ROOT set, default checkout present) -> the answer
    "a pruned export resolves to its live root": ("export", True, True, "live"),
    "a pruned export with no live root takes the default": ("export", False, True, "default"),
    "a removed worktree never takes a stray live root": ("worktree", True, True, "default"),
    "a removed worktree with nothing else stays as it was": ("worktree", True, False, "worktree"),
}


def _shell(env: dict[str, str]) -> str:
    r = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/lib/goh_root.sh"; goh_dir_repair; echo "$GOH_DIR"'],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    return r.stdout.strip()


def _python(env: dict[str, str]) -> str:
    r = subprocess.run(
        [sys.executable, "-c", PY_ROOT, str(REPO_ROOT / "lib")],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    return r.stdout.strip()


def _native(env: dict[str, str]) -> str:
    """The binary re-runs itself repaired and says where; nothing said is nothing repaired."""
    r = subprocess.run(
        [str(native_goh_path()), "--version"],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    said = re.search(r"-- using (\S+)", r.stderr)
    return said.group(1) if said else env["GOH_DIR"]


@pytest.mark.parametrize("reader", [_shell, _python, _native], ids=["sh", "py", "native"])
@pytest.mark.parametrize("case", sorted(CASES))
def test_every_reader_honours_the_live_root_only_for_an_export(tmp_path, case, reader):
    dead, live, default, want = CASES[case]
    env, names = _case(tmp_path, dead, live, default)
    assert reader(env) == str(names[want])


def test_a_live_goh_dir_is_never_repaired(tmp_path):
    """Calibration of the other side: a GOH_DIR that exists is the answer, export or not."""
    env, _ = _case(tmp_path, "worktree", True, True)
    env["GOH_DIR"] = str(_goh(tmp_path / "elsewhere"))
    assert {r(env) for r in (_shell, _python, _native)} == {str(tmp_path / "elsewhere")}


@pytest.mark.parametrize("shape", ["holds .goh-head", "pruned from the cache", "a checkout"])
def test_the_one_predicate(tmp_path, shape):
    """`goh_is_export` and `is_export` agree on what an export is."""
    cache = tmp_path / "head"
    d = {"holds .goh-head": tmp_path / "x", "pruned from the cache": cache / SHA}.get(
        shape, tmp_path / "co"
    )
    if shape == "holds .goh-head":
        d.mkdir()
        (d / ".goh-head").write_text(SHA + "\n")
    elif shape == "a checkout":
        (d / ".git").mkdir(parents=True)
    env = hermetic_env(GOH_HEAD_CACHE=str(cache))
    sh = subprocess.run(
        ["bash", "-c", f'. "{REPO_ROOT}/lib/goh_root.sh"; goh_is_export "$1"', "_", str(d)],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    ).returncode == 0  # fmt: skip
    py = subprocess.run(
        [sys.executable, "-c", PY_IS_EXPORT, str(REPO_ROOT / "lib"), str(d)],
        env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    ).returncode == 0  # fmt: skip
    assert (sh, py) == ((shape != "a checkout"),) * 2
