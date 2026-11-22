"""A redirection is no pgrep pattern (GOH.PGREPREDIRECT, `lib/self_match.py`): the self-match guard
read the `2` of `pgrep -P 52305 -lf 2>/dev/null` as `pgrep -f 2` and offered `goh wait pid` on every
process whose command line holds a 2; a heredoc quoting the same call, filed as text, met it too."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_claude_load_guard import _decide

CALL = "pgrep -P 52305 -lf 2>/dev/null"


@pytest.mark.parametrize(
    "command",
    [
        CALL,
        "pgrep -P 52305 -lf 2> /dev/null",
        "pgrep -u me -lf 2>&1 | head",
        f"cat > /tmp/x.md <<EOF\nThe guard refused `{CALL}` as pgrep -f 2\nEOF",
        f"cat > /tmp/x.md <<'EOF'\nThe guard refused `{CALL}` as pgrep -f 2\nEOF",
    ],
)
def test_a_redirection_or_a_heredoc_quoting_it_passes(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is None, command


def test_a_pattern_the_shell_holds_is_still_refused(tmp_path: Path) -> None:
    reason = _decide('pgrep -f "camoufox" 2>/dev/null || camoufox &', tmp_path)
    assert reason is not None and "camoufox" in reason, reason
