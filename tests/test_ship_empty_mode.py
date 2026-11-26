"""An empty value for a mode flag is refused, never read as "no flag" (GOH.SHIPEMPTYMODE).

Reported 2026-11-25 from token-pool: `goh ship --status "$f"` with `$f` empty (a glob that
matched nothing) STARTED A SHIP, because `if args.status:` read the empty string as absent and
fell through to `start`. A read became a release run. Each of ship's mode flags (--status,
--worker, --verify) now refuses an empty value with its reason and starts nothing.
"""

from __future__ import annotations

import sys

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import ship


@pytest.mark.parametrize("flag", ["--status", "--worker", "--verify"])
def test_an_empty_mode_flag_is_refused_and_starts_nothing(flag, monkeypatch, capsys) -> None:
    started = []
    monkeypatch.setattr(ship, "start", lambda args: started.append(args) or 0)
    with pytest.raises(SystemExit) as refused:
        ship.main([flag, ""])
    assert refused.value.code == 2
    assert started == [], "an empty mode flag must never start a ship"
    assert f"argument {flag}: needs a result file, not an empty value" in capsys.readouterr().err
