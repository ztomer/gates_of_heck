"""GOH.RULESLEFT: a spawn brief that enqueues a goh land names `goh land --check` before it.

`goh land --check "$PWD"` names every rule test and judge a land will hold the change to, in one
pass, before the enqueue's slot; lanes skipped it because no brief asked (GOH.SHAREZERO's 3-line
change, GOH.QUEUESCHEMA on the coordinator's word) and found each refusal one land at a time. The
spawn guard refuses a brief that asks its agent to `goh land --enqueue` and never names the check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_claude_agent_guard import DECL, GOOD, POOL, _allowed, _reason, _run, _spawn


@pytest.mark.parametrize(
    "ask",
    [
        'Land: run `goh land --enqueue --land "$PWD" --item X.Y`.',
        "Then $GOH_DIR/bin/goh land --enqueue --land . --item X.Y and stop.",
        'env -u SENTRY-TRACE goh land --enqueue --land "$PWD"',
    ],
)
def test_a_brief_enqueueing_a_land_without_the_check_is_refused(tmp_path: Path, ask: str) -> None:
    reason = _reason(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{ask}\n{DECL}\n{POOL}")), tmp_path))
    assert 'goh land --check "$PWD"' in reason, reason


@pytest.mark.parametrize(
    "clean",
    [
        'Before every enqueue run `goh land --check "$PWD"`; then `goh land --enqueue --land .`.',
        'Run goh land --check --rules "$PWD", then goh land --enqueue --land "$PWD".',
        "Commit and run the local gate; do not enqueue.",
        "Never run goh land --enqueue: the coordinator lands it.",
    ],
)
def test_a_brief_naming_the_check_or_never_enqueueing_runs(tmp_path: Path, clean: str) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"Do it.\n{clean}\n{DECL}\n{POOL}")), tmp_path))
