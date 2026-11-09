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


# GOH.CIJUDGEFP (2026-11-08): the judge reads a REQUEST aimed at the agent, not a keyword. A sentence
# that merely describes the check passes (the first live use refused the coordinator's own prompt
# describing it), and a negating word elsewhere in a sentence no longer excuses a real request.
@pytest.mark.parametrize(
    "describing",
    [
        "its judge_ci refuses a spawn that asks an agent to watch CI",
        "The guard refuses a prompt that asks the agent to wait for CI to go green.",
        "A spawn that says to poll CI until the run completes is refused by the judge.",
        "The judge refuses a report that names the CI run id and its conclusion.",
        "The refusal for a prompt that asks to watch the ship names the ruling.",
    ],
)
def test_a_sentence_that_only_describes_the_check_runs(tmp_path: Path, describing: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{describing}\n{DECL}\n{POOL}")), tmp_path))


@pytest.mark.parametrize(
    "ask",
    [
        "Leave out nothing: report the CI run id.",
        "Leave out nothing, report the CI run id.",
        "Report the CI run id, no other detail is needed.",
        "Do not skip anything, wait for CI to go green.",
        "Never stop early; wait for CI.",
        "Push, then wait for CI; do not ask me anything.",
        "Do not report early and watch CI until it finishes.",
        "You must wait for CI before reporting.",
        "Please poll CI until the run completes.",
        "Once pushed, report the CI verdict.",
        "Your report must include the CI run id.",
        "Tell the agent to watch CI.",
        "Do not guess. Run goh ship --wait and report each step.",
        "No excuses, wait for goh ship to finish.",
    ],
)
def test_a_real_ci_request_with_a_negating_word_elsewhere_is_refused(
    tmp_path: Path, ask: str
) -> None:
    reason = _reason(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{ask}\n{DECL}\n{POOL}")), tmp_path))
    assert "Refused" in reason, reason


@pytest.mark.parametrize(
    "clean",
    [
        "Do not watch, poll or wait for CI.",
        "Never wait for, watch or poll CI.",
        "Do not watch CI and do not poll it.",
        "Please do not wait for CI.",
        "You must not report the CI run id.",
        "Stop at push without waiting for CI.",
    ],
)
def test_a_negated_request_still_runs(tmp_path: Path, clean: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{clean}\n{DECL}\n{POOL}")), tmp_path))
