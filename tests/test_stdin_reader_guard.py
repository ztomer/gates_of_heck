"""A Bash command whose stdin reader has no input is refused at the tool boundary (GOH.STDINHANG).

THE CASE (2026-11-15). The sys_updater agent ran `sed ...; cat > /tmp/x.py 2>/dev/null; python3 -
$S <<'EOF' ... EOF`. The `cat` meant to write a file had no input of its own, so it read the
harness's stdin, which never closes: it hung 5h51m, the agent had long finished, and its task stayed
"running" until the coordinator killed the `cat`. Probed on this Mac (Claude Code 2.1.325): the
Bash tool runs `eval '<command>' < /dev/null` -- unless the command holds a heredoc, when the
`< /dev/null` is left off and fd 0 is the harness's unix socket. So an unfed reader beside a heredoc
hangs forever, and one without writes nothing. The guard (`lib/stdin_reader.py`) refuses a reader
(`cat` with no file operand, `tee`, `read`, `python3 -`, `sh -s`) that no heredoc, here-string, pipe
or `<` feeds. The escape is `# stdin-ok: <reason>`.
"""

from __future__ import annotations

import sys

import pytest
from test_claude_load_guard import _decide

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "lib"))
import shell_segments

HANG = (
    "sed -i '' 's/a/b/' /tmp/y.txt; cat > /tmp/x.py 2>/dev/null; python3 - $S <<'EOF'\n"
    "print('hi')\n"
    "EOF"
)


@pytest.mark.parametrize(
    "command",
    [
        HANG,  # the incident
        "cat > f",
        "tee f",
        "tee -a log.txt; echo done",
        "cat -n",
        "cat - > out.txt",
        "read -r line; echo $line",
        "IFS= read -r answer",
        "python3 - arg",
        "python -",
        "sh -s",
        "bash -s -- a b",
        "echo hi | cat; cat > g <<'EOF'\nx\nEOF\ncat > h",
        "while read -r l; do echo $l; done",
        "sudo tee /etc/x",
        "cat >> f && echo ok",
    ],
)
def test_a_stdin_reader_with_no_input_is_refused(tmp_path, command):
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert "stdin-ok" in reason and "5h51m" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "cat > f <<'EOF'\nhello\nEOF",  # a heredoc on that command
        "cat <<EOF > f\nhello $X\nEOF",
        "printf x | cat",  # piped
        "cmd | tee out.log",
        "make 2>&1 | tee -a build.log | tail -5",
        "cat < f",  # redirected
        "cat <f",
        "cat 0< f",
        "cat file",  # an operand
        "cat a b > c",
        "cat -n file.txt",
        "cat <(echo hi)",
        "ssh host 'bash -s' <<'EOF'\nuptime\nEOF",
        "ssh host bash -s < script.sh",
        # a heredoc on a different command, the reader fed
        "echo hi | cat > f; python3 - <<'EOF'\nprint(1)\nEOF",
        'cat <<< "$x"',  # a here-string
        'read -r a b <<< "$line"',
        "tee f <<< hi",
        "python3 - <<'EOF'\nprint(1)\nEOF",
        "python3 -c 'import sys' -",
        "python3 script.py -",
        "bash script.sh",
        "bash -c 'cat'",
        "while read -r l; do echo $l; done < list.txt",
        "find . -name '*.py' | while read -r f; do wc -l \"$f\"; done",
        "while IFS= read -r l; do echo $l; done <<'EOF'\na\nb\nEOF",
        "{ cat; echo; } < f",
        "(cat) < f",
        "read -t 5 x",  # bounded
        "read -u 3 x",  # another fd
        "timeout 5 cat",
        "cat &",  # a background list reads /dev/null
        "echo 'cat > f'",  # text, not code
        "git log --oneline | head",
        "cat > f # stdin-ok: I want an empty file and know the heredoc rule",
    ],
)
def test_a_fed_or_bounded_reader_runs(tmp_path, command):
    assert _decide(command, tmp_path) is None, command


def test_a_here_string_is_not_a_heredoc_opener():
    """`<<<` is a here-string: the splitter read its word as a heredoc delimiter and swallowed the
    rest of the command as a body (the commands after it went unjudged)."""
    segs, bodies = shell_segments.split('cat <<< "$x"; rm -rf ~/y\necho z')
    assert bodies == {}, bodies
    assert ["rm", "-rf", "~/y"] in segs, segs
