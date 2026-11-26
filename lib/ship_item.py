"""goh ship's refusals that its LAST steps used to make, asked before its first (GOH.SHIPITEM).

* the item: on 2026-11-12 three goh releases (0.106.1-0.106.3) shipped under RM.MONITOR, whose repo
  is monitor, and only the done step refused it, after a 40-minute verify. `loopctl shippable ID
  --repo NAME` answers with done's own rule: an unknown item, a done one, or another repo's.
* the hooks: koffee_big 2.17.5 pushed, then its release's bump commit refused '.githooks/pre-commit
  is an OLDER stock hook'. The reading is structural.sh's own (lib/hook_currency.py, which
  gates/_hook_currency.sh runs), so the two never disagree on what is stale.
* the lock: on 2026-11-22 a ship's worker found `.ship.lock` held by a bisecting ship and failed
  "at push", naming nobody; a held lock now refuses before anything starts, naming its holder's
  record, pid and state (lib/ship_lock.py, GOH.SHIPBISECTCOV).
* the suite's work: no ship while the newest whole run is over a `whole_run` ceiling
  (`work_refusal`, lib/test_work.py, GOH.SUITETOTAL), nor while the quiet lands' median gate is
  over its goal (lib/gate_goal.py).

Each returns why the ship is refused, or None. Pinned by tests/test_ship_item.py.

goh ship's refusals of what its LAST steps refused (GOH.SHIPITEM), asked before its first: an
`--item` that `loopctl shippable ID --repo NAME` refuses (unknown, done, or another repo's;
RM.MONITOR shipped three goh releases and only done refused) and an OLDER stock hook in the repo
(`lib/hook_currency.py`, structural.sh's own reading), which the release's commits met after the
push. Pinned by `test_ship_item.py`.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


def _env() -> dict[str, str]:
    """This environment without git's repository bindings (contract #12)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
    from _gitutil import local_env_vars

    bound = set(local_env_vars())
    return {k: v for k, v in os.environ.items() if k not in bound}


def item_refusal(repo: Path, loopctl: Path, item: str, name: str) -> str | None:
    if not loopctl.is_file():
        return (f"--item {item}: no loopctl at {loopctl} (GOH_LOOPCTL) to read the item from, so "
                "its done step could not record it")  # fmt: skip
    done = subprocess.run([sys.executable, str(loopctl), "shippable", item, "--repo", name],
                          cwd=repo, env=_env(), capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False, timeout=120)  # fmt: skip
    if done.returncode == 0:
        return None
    said = [ln.strip() for ln in (done.stderr + done.stdout).splitlines() if ln.strip()][-4:]
    return (f"--item {item}: loopctl shippable refuses it (exit {done.returncode}): "
            + " | ".join(said or ["(it printed nothing)"]))  # fmt: skip


def hook_refusal(repo: Path, goh_root: Path) -> str | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hook_currency

    stale = hook_currency.refused(repo, goh_root, _env())
    if not stale:
        return None
    return (f"{repo.name}: {', '.join(stale)} is an OLDER stock hook, which refuses the release's "
            f"commits after the push -- update the hooks, then commit them: {goh_root}/install.sh "
            f"{shlex.quote(str(repo))}")  # fmt: skip


def lock_refusal(result: Path) -> str | None:
    """A ship whose RESULT's directory holds a held `.ship.lock` would be refused by its worker:
    say so now, naming the holder's record and state (lib/ship_lock.py, GOH.SHIPBISECTCOV)."""
    import ship_lock  # lib/, here: only a ship needs the lock table

    lock = result.parent / ".ship.lock"
    who = ship_lock.holder(lock)
    return f"another goh ship holds {lock}: {who}" if who else None


def work_refusal(repo: Path) -> str | None:
    """A repo whose suite holds whole-run ceilings (tests/test_work_ceiling.json `whole_run`) ships
    only while its newest recorded whole run is under them (lib/test_work.py, GOH.SUITETOTAL)."""
    ceilings = repo / "tests" / "test_work_ceiling.json"
    if not ceilings.is_file():
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import json

    import test_work

    conf = json.loads(ceilings.read_text(encoding="utf-8"))
    if why := test_work.ship_refusal(conf):
        return why
    import gate_goal

    got = gate_goal.current(conf, repo.resolve().name)
    if got is not None and got.median_s is None:
        sys.stderr.write(f"⚠ {got.line()}\n")  # not judged, said: never a silent pass
    if got is not None and got.over:
        return f"{got.line()}: no ship while the gate is over its goal on a quiet machine"
    return None
