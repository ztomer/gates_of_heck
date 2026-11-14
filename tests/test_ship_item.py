"""goh ship refuses what its LAST step would refuse before its FIRST (GOH.SHIPITEM).

Three goh releases (0.106.1-0.106.3) shipped under RM.MONITOR, whose repo is monitor, and only the
done step refused it, after a 40-minute verify. koffee_big 2.17.5 pushed, then its release's bump
commit refused '.githooks/pre-commit is an OLDER stock hook'. So with --item, preflight asks
`loopctl shippable ID --repo NAME` (unknown, done, or another repo's item: refused, nothing
run), and an older stock hook in the repo is refused there, naming install.sh.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import test_ship
from conftest import REPO_ROOT, git
from test_ship import FAKE_LOOPCTL, _calls, _ship

shipped = test_ship.shipped  # the fixture, by assignment (an import would be shadowed by each arg)

# The fake's `shippable` exits $FAKE_SHIPPABLE_RC, saying why, as loopctl does.
FAKE = (
    FAKE_LOOPCTL
    + """\
if sys.argv[1] == "shippable" and os.environ.get("FAKE_SHIPPABLE_RC", "0") != "0":
    i, name = sys.argv[2], sys.argv[4]
    print(f"✗ {i}: shipped {name}, but {i}'s repo is monitor", file=sys.stderr)
    sys.exit(int(os.environ["FAKE_SHIPPABLE_RC"]))
"""
)


def _remote_head(repo: Path) -> str:
    return git(repo, "rev-parse", "origin/main").strip()


def test_an_item_of_another_repo_is_refused_before_anything_runs(shipped, tmp_path):
    (tmp_path / "loopctl.py").write_text(FAKE)
    before = _remote_head(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "RM.MONITOR", FAKE_SHIPPABLE_RC="1")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "RM.MONITOR's repo is monitor" in r.stderr and "nothing started" in r.stderr, r.stderr
    assert [c[0] for c in _calls(tmp_path)] == ["shippable"], _calls(tmp_path)
    assert git(shipped, "ls-remote", "origin", "main").split()[0] == before
    assert not (tmp_path / "ship.json").exists()


def test_the_items_repo_is_asked_by_the_name_ship_records(shipped, tmp_path):
    (tmp_path / "loopctl.py").write_text(FAKE)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE")
    assert r.returncode == 0, r.stdout + r.stderr
    calls = _calls(tmp_path)
    assert calls[0] == ["shippable", "X.ONE", "--repo", shipped.name], calls
    assert calls[-1][:2] == ["done", "X.ONE"], calls


def test_an_item_with_no_loopctl_to_ask_is_refused(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GOH_LOOPCTL=str(tmp_path / "none.py"))
    assert r.returncode == 2 and "no loopctl" in r.stderr, r.stdout + r.stderr


def test_an_older_stock_hook_is_refused_before_the_push(shipped, tmp_path):
    show = ["git", "-C", str(REPO_ROOT), "cat-file", "-p", "73391f0:hooks/pre-push"]
    old = subprocess.run(show, capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout
    (shipped / ".githooks").mkdir()
    (shipped / ".githooks" / "pre-push").write_bytes(old)
    git(shipped, "add", ".githooks")
    git(shipped, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "chore: hooks")
    before = _remote_head(shipped)
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "OLDER stock hook" in r.stderr and "install.sh" in r.stderr, r.stderr
    assert git(shipped, "ls-remote", "origin", "main").split()[0] == before
