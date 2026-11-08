"""GOH.ENDATPUSH (owner ruling 2026-11-08): agents end at push; CI is the coordinator's gate.

The report contract asks for the commits and whether they are pushed and tagged, never a CI run id,
and a write spawn that asks its agent to wait for, watch or poll CI is refused with the reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_claude_agent_guard import CONTRACT, DECL, GOOD, POOL, _allowed, _reason, _run, _spawn


def test_the_contract_asks_for_push_and_tag_never_for_ci() -> None:
    """Agents end at push (owner ruling 2026-11-08, GOH.ENDATPUSH): CI is `loopctl done`'s gate."""
    assert "CI" not in CONTRACT and "run id" not in CONTRACT and "conclusion" not in CONTRACT
    assert "pushed" in CONTRACT and "tagged" in CONTRACT


@pytest.mark.parametrize(
    "ask",
    [
        "Report the CI run id and its conclusion.",
        "Then give the CI run id.",
        "Wait for CI to go green before reporting.",
        "Watch CI with gh run watch until it finishes.",
        "Poll CI until the run completes.",
        "Report the CI verdict.",
        "then `gh run view` the result",
    ],
)
def test_a_spawn_asking_the_agent_to_wait_on_ci_is_refused(tmp_path: Path, ask: str) -> None:
    reason = _reason(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{ask}\n{DECL}\n{POOL}")), tmp_path))
    assert "loopctl done" in reason, reason


@pytest.mark.parametrize(
    "clean",
    [
        "Run ./release.sh to cut the release.",
        "Fix the CI workflow file so its lint step runs.",
        "Report the commits and whether they are pushed and tagged.",
        "Once pushed, STOP. Do not watch CI, and leave any CI run id out of your report.",
        "Never wait for CI; never poll `gh run view`.",
    ],
)
def test_a_spawn_that_does_not_ask_for_ci_runs(tmp_path: Path, clean: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{clean}\n{DECL}\n{POOL}")), tmp_path))


def test_the_contract_hands_off_at_goh_ship_started_for_private_repos() -> None:
    """GOH.SHIP (2026-11-08): a private repo's agent ends at "committed, local gate green, goh ship
    started"; a public repo's keeps standard workflows and ends at push and tag (CI.PUBLIC)."""
    assert "goh ship" in CONTRACT and "started" in CONTRACT
    assert "pushed" in CONTRACT and "tagged" in CONTRACT


@pytest.mark.parametrize(
    "ask",
    [
        "Run goh ship --wait and report each step.",
        "Wait for goh ship to finish, then report.",
        "Watch the ship until it lands.",
        "Poll goh ship --status until it is done.",
    ],
)
def test_a_spawn_asking_the_agent_to_wait_on_its_ship_is_refused(tmp_path: Path, ask: str) -> None:
    reason = _reason(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{ask}\n{DECL}\n{POOL}")), tmp_path))
    assert "goh ship" in reason, reason


@pytest.mark.parametrize(
    "clean",
    [
        "Commit, run the local gate, then start goh ship --item X.Y and stop.",
        "Report the goh ship result path.",
    ],
)
def test_a_spawn_that_starts_goh_ship_and_stops_runs(tmp_path: Path, clean: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{clean}\n{DECL}\n{POOL}")), tmp_path))
