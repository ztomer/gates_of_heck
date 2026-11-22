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
import stdin_reader

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


# GOH.STDINFILTERS: a FILTER -- a program that reads its stdin when its argv names no input file
# -- is a reader by the same rule as `cat`: its script operand (grep's pattern, jq's, sed's and
# awk's program) is not a file, its options' values are not operands, and an option that names the
# input or reads none (`grep -r`, `jq -n`, `sed -i`, `xargs -a`) makes it no reader.
@pytest.mark.parametrize(
    "command",
    [
        "grep foo",
        "grep -c pattern",
        "grep -e a -e b",
        "grep -A 3 foo",
        "egrep 'a|b'",
        "jq .",
        "jq -r '.x'",
        "jq --arg k v '.a'",
        "jq -r --args '.[]' a b",
        "xargs rm",
        "xargs -n1 echo",
        "sed 's/a/b/'",
        "sed -n -e p",
        "awk '{print $1}'",
        "awk -F: '{print}'",
        "awk 'BEGIN{x=1} {print x}'",
        "sort -u",
        "sort -k2 -t,",
        "wc -l",
        "head -5",
        "head -n 3",
        "tail -n 1",
        "cut -d, -f1",
        "tr a-z A-Z",
        "uniq -c",
        "x=1; grep -q foo && echo yes",
    ],
)
def test_a_filter_with_no_file_operand_and_no_input_is_refused(tmp_path, command):
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert "nothing feeds it" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "grep foo file",
        "grep -r foo",
        "grep -rn foo .",
        "grep -e x -- f",
        "grep -f pats.txt file",
        "grep -A 3 foo file",
        'grep -q x <<< "$y"',
        "git log | grep fix",
        "jq . f.json",
        "jq -n '1'",
        "jq -nr '1'",
        "jq --arg k v '.a' f.json",
        "jq -f prog.jq in.json",
        "jq --version",
        "xargs -a list echo",
        "find . -name '*.py' | xargs grep foo",
        "sed -i '' 's/a/b/' f",
        "sed 's/a/b/' f",
        "sed -n p f",
        "awk 'BEGIN{print 1}'",
        "awk -v x=1 'BEGIN { print x }'",
        "awk '{print}' f",
        "awk -f p.awk f",
        "awk '{print}' x=1 f",
        "sort f",
        "sort -o out f",
        "wc -l f",
        "wc -l < f",
        "head -5 f",
        "tail -f log",
        "cut -d, -f1 f.csv",
        "tr a b < f",
        "echo x | tr a b",
        "uniq in out",
        "grep foo # stdin-ok: testing the escape",
    ],
)
def test_a_filter_with_a_file_or_input_runs(tmp_path, command):
    assert _decide(command, tmp_path) is None, command


def test_an_empty_quoted_word_is_a_word_not_a_separator():
    """`grep -c "" f` split as `grep -c` and `f`: shlex yields the empty word as '', and the
    separator test (`set(tok) <= set(";&|\\n")`) holds for the empty set (GOH.STDINFILTERS)."""
    segs, _ = shell_segments.split("grep -c \"\" f; rm -rf '' ~/y")
    assert segs == [["grep", "-c", "", "f"], ["rm", "-rf", "", "~/y"]], segs


# GOH.STDINGUARDFP: the splitter read a QUOTED separator as a separator (`tr '\n' ';' < $L` split at
# the `';'`, so the `< $L` stood alone as another command and `tr` looked unfed), dropped what a
# comment left on its line into the next line's command, and read a redirect glued to a word
# (`tr a b<$F`) as part of the word. The guard refused commands that cannot read the terminal.
@pytest.mark.parametrize(
    "command",
    [
        "tr '\\n' ';' < $L",  # the incident: a quoted `;` is a word
        'tr a b <"$F"',
        "tr a b < $L",
        "tr a b <$F",
        "tr a b<$F",  # glued to its last word
        'tr a b<"$F"',
        "tr -d ';' < f",
        'tr "|" " " < f',
        "tr '&' x <f | sort",
        "cat < f | tr a b",
        "echo x | tr a b < f",  # a redirect inside a pipeline stage that reads stdin
        "sed 's/a/b/' < f | tr '|' ' ' | wc -l",
        "tr a b <<< x",  # a here-string feeds it
        "tr a b<<<x",
        "tr a b <<EOF\nx\nEOF",  # a heredoc feeds it
        "tr a b < f # a comment\n: ok",  # the comment is cut, the next line is its own command
        "tr a b <&3",
        "tr a b 2>&1 < f",  # `>&` is a redirection operator, not a separator
        "tr a b &>/dev/null < f",
        "tr a b >| out < f",
        "tr a b 2>/dev/null < f",
        "tr a b 0< f",
        "tr a b < f 2>&1",
    ],
)
def test_a_redirect_on_the_reader_feeds_it_whatever_is_quoted_around_it(tmp_path, command):
    assert _decide(command, tmp_path) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "tr a b",
        "tr '\\n' ';'",  # a quoted `;` is not a redirect
        "cat",
        "tr a b # no < in a comment feeds nothing",
        "ls # note\ncat",  # a comment does not join the next line's command to this one
        "echo ';' | cat > f; tr a b",
        "tr a b > out",  # an output redirect feeds nothing
        "tr a b 2>err",
        "tr a b '<' f",  # a quoted `<` is text
        "echo 'a < b'; cat",
    ],
)
def test_a_reader_with_no_input_is_still_refused_beside_quoted_separators(tmp_path, command):
    reason = _decide(command, tmp_path)
    assert reason is not None, command
    assert "stdin-ok" in reason, reason


def test_a_quoted_separator_is_one_word_of_its_command():
    segs, _ = shell_segments.split("tr '\\n' ';' < $L; echo \"|\" & find . -exec x {} \\;")
    assert segs == [
        ["tr", "\\n", ";", "<", "$L"],
        ["echo", "|"],
        ["find", ".", "-exec", "x", "{}", ";"],
    ], segs


def test_a_redirect_glued_to_a_word_is_its_own_token_and_a_quoted_one_is_not():
    segs, _ = shell_segments.split("tr a b<$F; echo 'a<b' 2>&1 a<<<x 3>|f")
    assert segs[0] == ["tr", "a", "b", "<$F"], segs
    assert segs[1] == ["echo", "a<b", "2>&1", "a", "<<<x", "3>|f"], segs
    assert isinstance(segs[1][1], shell_segments.Literal)
    assert not isinstance(segs[1][2], shell_segments.Literal)


@pytest.mark.parametrize("command", ['grep "8>&-" f.txt', "grep -n '8>&- x' f.txt"])
def test_a_quoted_redirect_like_word_is_a_word_to_every_reader(command: str) -> None:
    """GOH.SPLITQUOTE: shlex (posix) drops quoting, so a quoted `8>&-` read as a redirect and the
    grep read as unfed (the one false refusal in 13,692 transcript commands, GOH.STDINFILTERS)."""
    segs, _ = shell_segments.split_ops(command)
    assert all(isinstance(w, shell_segments.Literal) for w in segs[0][0] if ">" in w), segs
    assert stdin_reader.judge(command) is None, command
