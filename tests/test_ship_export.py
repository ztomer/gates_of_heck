"""goh ship's verify judges a clean export of the shipped sha, never the live checkout
(GOH.CLASS.CHECKOUT, lib/ship_export.py).

v0.116.0's ship failed at verify on a `bin/goh.*.staging` another build wrote in the live checkout
("working tree: 1 uncommitted"): the card read the shared tree, not what shipped. The card now runs
with LOOPCTL_ROOT at a temp root holding a `git clone --shared` of the repository at the shipped
sha, its branch tracking the ship's remote under the same name and URL, and the repository's own
pre-push hook. The fake loopctl's card fails on any `git status` output in the checkout it is
pointed at, as the real card does, and records where it looked.
"""

from __future__ import annotations

import json
from pathlib import Path

import test_ship
from conftest import git
from test_ship import FAKE_LOOPCTL, _ship

shipped = test_ship.shipped  # the fixture, by assignment
# The card: where it was pointed (LOOPCTL_ROOT/NAME), what it found there; red on a dirty tree.
CARD = """\
import json, os, subprocess, sys
if sys.argv[1] == "verify":
    full = os.path.join(os.environ["LOOPCTL_ROOT"], sys.argv[2])
    def g(*a):
        run = subprocess.run(["git", "-C", full, *a], text=True, capture_output=True)
        return run.stdout.strip()
    hook = os.path.join(full, g("rev-parse", "--git-path", "hooks/pre-push"))
    seen = {"full": full, "cwd": os.getcwd(), "head": g("rev-parse", "HEAD"),
            "status": g("status", "--porcelain"),
            "upstream": g("rev-parse", "--abbrev-ref", "@{u}"),
            "url": g("remote", "get-url", "origin"), "tags": g("tag", "--points-at", "HEAD"),
            "hook": os.path.isfile(hook)}
    open(os.environ["CARD_SEEN"], "w").write(json.dumps(seen))
    if seen["status"]:
        os.environ.update(FAKE_VERIFY_RC="1", FAKE_CARD_ERR="working tree: 1 uncommitted")
"""
STRAY = "bin/goh.4242.staging"


def test_a_stray_in_the_live_checkout_does_not_fail_the_card(shipped: Path, tmp_path: Path):
    (tmp_path / "loopctl.py").write_text(CARD + FAKE_LOOPCTL)
    hook = Path(git(shipped, "rev-parse", "--absolute-git-dir").strip()) / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0  # push_gate.sh\n")
    hook.chmod(0o755)
    install = f"mkdir -p bin && echo half > {STRAY}"  # a concurrent build's stray, left behind
    seen_at = tmp_path / "card.json"
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GOH_SHIP_INSTALL=install,
              CARD_SEEN=str(seen_at))  # fmt: skip
    res = json.loads((tmp_path / "ship.json").read_text())
    assert r.returncode == 0, r.stdout + r.stderr + str(res.get("tail"))
    assert (shipped / STRAY).is_file()  # the live tree is still dirty: the card never read it
    seen = json.loads(seen_at.read_text())
    assert seen["status"] == "", seen
    assert seen["head"] == res["sha"], seen
    assert seen["upstream"] == "origin/main", seen
    assert seen["url"] == git(shipped, "remote", "get-url", "origin").strip(), seen
    assert seen["tags"] == f"v{res['version']}", seen
    assert seen["hook"], seen
    assert not Path(seen["full"]).resolve().is_relative_to(shipped.resolve()), seen
    assert not Path(seen["full"]).exists(), "the export outlived the verify step"
    assert Path(seen["cwd"]).name == Path(seen["full"]).name, seen
