"""goh ship's two refusals that its LAST steps used to make, asked before its first (GOH.SHIPITEM).

* the item: on 2026-11-12 three goh releases (0.106.1-0.106.3) shipped under RM.MONITOR, whose repo
  is monitor, and only the done step refused it, after a 40-minute verify. `loopctl shippable ID
  --repo NAME` answers with done's own rule: an unknown item, a done one, or another repo's.
* the hooks: koffee_big 2.17.5 pushed, then its release's bump commit refused '.githooks/pre-commit
  is an OLDER stock hook'. The reading is structural.sh's own (gates/_hook_currency.sh), so the two
  never disagree on what is stale.

Each returns why the ship is refused, or None. Pinned by tests/test_ship_item.py.
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
    lib = shlex.quote(str(goh_root / "gates" / "_hook_currency.sh"))
    script = f"GOH_ROOT={shlex.quote(str(goh_root))}; . {lib}; goh_stale_hooks"
    done = subprocess.run(["bash", "-c", script], cwd=repo, env=_env(), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
    stale = done.stdout.split()
    if not stale:
        return None
    return (f"{repo.name}: {', '.join(stale)} is an OLDER stock hook, which refuses the release's "
            f"commits after the push -- update the hooks, then commit them: {goh_root}/install.sh "
            f"{shlex.quote(str(repo))}")  # fmt: skip
