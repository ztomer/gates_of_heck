"""lib/preflight_disk.py and its use by push_gate.sh: a short disk refuses the push AS a short disk,
before any export is made, and only when the repo declares GOH_MIN_FREE_GIB."""

import subprocess
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
from preflight_disk import GIB, shortfall  # noqa: E402

TOOL = REPO_ROOT / "lib" / "preflight_disk.py"


def test_the_arithmetic_both_directions() -> None:
    assert shortfall(20 * GIB, 15) is None
    assert shortfall(15 * GIB, 15) is None
    assert shortfall(15 * GIB - 1, 15) is not None
    why = shortfall(10 * GIB, 15) or ""
    assert "10.0 GiB free" in why and "15 GiB" in why


def test_the_command_refuses_a_need_no_volume_has_and_rejects_nonsense(tmp_path) -> None:
    r = subprocess.run(
        [sys.executable, str(TOOL), str(tmp_path), "1e9"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "GiB free" in r.stderr, r.stderr
    assert subprocess.run([sys.executable, str(TOOL), str(tmp_path), "0"]).returncode == 0
    assert subprocess.run([sys.executable, str(TOOL), str(tmp_path), "lots"]).returncode == 2


def test_the_push_gate_refuses_before_any_export(tmp_path) -> None:
    from test_push_gate import _push, _repo

    repo = _repo(tmp_path, gatesrc="GOH_MIN_FREE_GIB=1e9\n")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()  # fmt: skip
    rc, report, out = _push(repo, sha, tmp_path)
    assert rc == 1, out
    assert "GiB free" in out and report == "", out  # the gate never ran: no report written
    (tmp_path / "ok").mkdir()
    repo2 = _repo(tmp_path / "ok", gatesrc="")
    sha2 = subprocess.run(["git", "-C", str(repo2), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()  # fmt: skip
    assert _push(repo2, sha2, tmp_path / "ok")[0] == 0
