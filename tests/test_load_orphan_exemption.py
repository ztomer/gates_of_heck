"""The RECORDED EXEMPTIONS: the name lookups the house's kill-by-name rule allows.

THE RULE. A name is not an owner -- zinc's harness SIGKILLed another session's browser with a
`pkill -9 -f` in the middle of a reply (2026-09-23) -- so the house forbids killing by name
(`goh kill-by-name`), and an exemption is a RECORD: one file, one exact call, a reason, and a
stale entry fails.

THE EXEMPTIONS (2026-10-20 and 2026-10-21). The backstop kills the spinners whose owner is gone,
and it finds them by NAME, twice over:

  * the SECOND shape (2026-10-20): a load experiment's registry can VANISH -- it lives in the
    experiment's directory, and that directory is pruned, and the spinner is left burning a core
    each with nothing naming it (eight of them, orphaned by a pytest that died, each about an
    hour of CPU). The registry cannot reach those, so the backstop asks the ONE shape the house
    spawns: is this process's command line EXACTLY `load_registry.SPINNER_COMMAND`, is its parent
    1, and is the owner its release pipe names gone?
  * the THIRD shape (2026-10-21): twenty-four hand-rolled spinners burned four cores each for
    four and a half hours, and the backstop knew only the house's own command line, so it left
    every one of them alone. So the backstop asks a shape it can NAME without owning it: an
    ORPHAN that has BURNED a core on a busy loop.

Both questions need a name lookup, and a name lookup is what the house forbids -- so both are
RECORDED EXEMPTIONS, with the lookup and the kill on ONE line, so the rule sees them instead of
a rewrite hiding them.

WHAT IS PINNED HERE, IN BOTH DIRECTIONS.

  * the gate that guards the allowlist is GREEN with the entries, and RED without them: the
    exemptions are load-bearing, and the house's own rule says so;
  * a STALE entry -- the call changed, or is gone -- is RED, and so is a BROADENED call whose
    entry was not re-keyed: the rule sees both;
  * a BROADENED call whose entry WAS re-keyed is green to the rule -- the rule matches a call,
    not what a call does -- and RED here, because the entry must still name the narrow call, and
    the filter must still ask its questions and no more;
  * the house's own shape is matched WHOLE: a command line that starts the same way, or mentions
    the same words, is somebody else's process.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, run_goh, write

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap
import load_registry as reg

ALLOW_FILE = REPO_ROOT / "kill_by_name_allow.json"
LIBRARY = REPO_ROOT / "lib" / "load_reap.py"

# The command names are assembled from parts, because this gate scans this file too: a fixture
# line naming the command is a kill by name to the gate (`tests/test_check_no_kill_by_name.py`
# does the same, and says why).
LOOKUP = "pg" + "rep"
KILL = "ki" + "ll"

# The two recorded exemptions, each named by the pattern its lookup feeds.
SHAPE_EXEMPTION = "ORPHAN_PATTERN"
BURNING_EXEMPTION = "CANDIDATE_PATTERN"
EXEMPTIONS = (SHAPE_EXEMPTION, BURNING_EXEMPTION)

# The command line of a spinner of the house's shape, and the owner its pipe names.
FIFO = "/tmp/goh-exempt/release-999999-0.fifo"
EXACT_COMMAND = f"/bin/sh -c {reg.spinner_command(FIFO)}"


def _exempted_call(pattern: str) -> str:
    """The call an exemption names: the one line with the lookup, the kill and the pattern.

    READ from the library, not written here: a copy in this file would be a second definition of
    the call, and it would drift the moment the call moved.
    """
    for line in LIBRARY.read_text(encoding="utf-8").splitlines():
        if LOOKUP + "(" in line and KILL + "(" in line and pattern in line:
            return line.strip()
    raise AssertionError(f"the library has no line with the lookup, the kill and {pattern} on it")


def _entries() -> list[dict]:
    data = json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
    return list(data["entries"])


def _ours() -> list[dict]:
    return [entry for entry in _entries() if entry["path"] == "lib/load_reap.py"]


def _entry(pattern: str) -> dict:
    for entry in _ours():
        if pattern in entry["line"]:
            return entry
    raise AssertionError(f"no exemption names the call with {pattern} on it")


def _fixture(repo: Path, library: str, allow: str) -> None:
    """A scratch repository with one code file and one allowlist: what the rule reads."""
    write(repo, "lib/load_reap.py", library)
    write(repo, "kill_by_name_allow.json", allow)
    commit_all(repo)


def _allow(*lines: str) -> str:
    """An allowlist naming these calls, and only these."""
    return json.dumps(
        {
            "entries": [
                {
                    "path": "lib/load_reap.py",
                    "line": line,
                    "status": "legitimate",
                    "reason": "the recorded exemption, for the test",
                }
                for line in lines
            ]
        },
        indent=1,
    )


def _calls() -> list[str]:
    """Every recorded call, read from the library."""
    return [_exempted_call(pattern) for pattern in EXEMPTIONS]


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_the_exemption_is_one_entry_with_its_reason_written_down(pattern: str) -> None:
    """One file, one exact call, a reason: the shape of an exemption the house can read."""
    entry = _entry(pattern)
    assert entry["line"] == _exempted_call(pattern), entry
    assert entry["status"] == "legitimate", entry
    assert "shape" in entry["reason"] or "busy loop" in entry["reason"], entry
    assert len(entry["reason"]) > 80, "the reason says WHY, in words"


def test_every_exemption_names_a_distinct_call() -> None:
    """Two exemptions, two calls: an entry that names the same call twice is one entry too many."""
    lines = [entry["line"] for entry in _ours()]
    assert len(lines) == len(set(lines)), lines
    assert len(lines) == len(EXEMPTIONS), lines


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_the_exemption_still_matches_the_call_it_excuses(pattern: str) -> None:
    """A stale entry fails the house's rule. So the entry has to name the call that is IN the
    file: the one lookup, the one filter and the one kill, on one line."""
    source = LIBRARY.read_text(encoding="utf-8")
    assert _exempted_call(pattern) in source, (
        "the exempted call is not the call in the file any more"
    )
    tree = ast.parse(source)
    lookups = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == LOOKUP
    ]
    filters = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in ("our_orphans", "burning_orphans")
    ]
    assert len(lookups) == len(EXEMPTIONS), lookups
    assert len(filters) == len(EXEMPTIONS), filters
    exempted = [node for node in filters if pattern in ast.unparse(node)]
    assert len(exempted) == 1, exempted
    assert exempted[0].lineno in [node.lineno for node in lookups], (
        "the lookup and the filter are on one line"
    )


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_the_rule_is_green_with_the_exemption_and_red_without_it(repo: Path, pattern: str) -> None:
    """The exemption is load-bearing, and the house's own rule says so: the same file is green
    with the entry in `kill_by_name_allow.json` and red without it."""
    library = LIBRARY.read_text(encoding="utf-8")
    _fixture(repo, library, _allow(*_calls()))
    excused = run_goh(repo, "kill-by-name")
    assert excused.returncode == 0, excused.stdout + excused.stderr
    assert "allowlisted" in excused.stdout, excused.stdout

    others = [call for call in _calls() if pattern not in call]
    _fixture(repo, library, _allow(*others))
    unexcused = run_goh(repo, "kill-by-name")
    assert unexcused.returncode == 1, "the rule does not see the lookup without the entry"
    assert f"a {LOOKUP} lookup feeds a {KILL}" in unexcused.stdout, unexcused.stdout


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_a_stale_exemption_is_red(repo: Path, pattern: str) -> None:
    """The call changed and the entry did not: the rule sees a lookup nothing excuses, and an
    entry that matches no kill at all."""
    narrowed = _exempted_call(pattern)
    others = [call for call in _calls() if pattern not in call]
    library = LIBRARY.read_text(encoding="utf-8").replace(
        narrowed,
        f"outcomes = [(pid, {KILL}(pid)) for pid in candidates]",
    )
    _fixture(repo, library, _allow(narrowed, *others))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 1, "a stale entry is green: the ratchet does not bite"
    assert "stale" in result.stdout, result.stdout


def _broadened(pattern: str) -> str:
    """The same call with the shape filter taken out: the lookup feeds the kill DIRECTLY."""
    return f"outcomes = [(pid, {KILL}(pid)) for pid in {LOOKUP}({pattern})]"


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_a_broadened_call_without_a_re_key_is_red(repo: Path, pattern: str) -> None:
    """The lookup no longer goes through the house's shape, and the entry was left alone: the
    rule sees a lookup nothing excuses, and an entry that matches no kill at all."""
    narrowed = _exempted_call(pattern)
    others = [call for call in _calls() if pattern not in call]
    library = LIBRARY.read_text(encoding="utf-8").replace(narrowed, _broadened(pattern))
    _fixture(repo, library, _allow(narrowed, *others))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 1, "a broadened call is green: the ratchet does not bite"
    assert "stale" in result.stdout, result.stdout


@pytest.mark.parametrize("pattern", EXEMPTIONS)
def test_a_re_keyed_exemption_is_green_to_the_rule_and_caught_by_the_pin(
    repo: Path, pattern: str
) -> None:
    """The rule's LIMIT, stated: it matches a CALL, not what a call does, so a broadened call
    whose entry was re-keyed is green to it. What catches that is the pin -- the entry has to
    name the NARROW call, and the narrow call has to be the one in the file."""
    narrowed = _exempted_call(pattern)
    broadened = _broadened(pattern)
    others = [call for call in _calls() if pattern not in call]
    library = LIBRARY.read_text(encoding="utf-8").replace(narrowed, broadened)
    _fixture(repo, library, _allow(broadened, *others))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 0, "the rule matches the call it is given"
    # The pin, and the two facts it rests on. A broadened file with a re-keyed entry breaks both,
    # so the pin is RED where the rule is green.
    assert narrowed != broadened
    assert narrowed not in library, "the broadened file no longer carries the exempted call"
    assert _entry(pattern)["line"] == narrowed, "the entry still names the narrow call"


def test_the_shape_is_matched_whole() -> None:
    """The exemption's scope: the house's shape is a WHOLE command line. A command line that
    starts the same way, or mentions the same words, is somebody else's process."""
    assert reg.SHAPE.pattern.startswith("^"), reg.SHAPE.pattern
    assert reg.SHAPE.pattern.endswith("$"), reg.SHAPE.pattern
    assert reg.spinner_shape(EXACT_COMMAND) == (FIFO, 999999)
    assert reg.spinner_shape(EXACT_COMMAND + "; echo and more") is None, "a prefix is not the shape"
    assert reg.spinner_shape(f"echo {EXACT_COMMAND}") is None, "a mention is not the shape"
    assert reg.spinner_shape(EXACT_COMMAND.replace('"', "")) is None, "the quotes are part of it"
    assert reg.spinner_shape("/bin/sh -c sleep 300") is None


def test_the_shape_filter_asks_three_questions_and_no_more(monkeypatch) -> None:
    """The exemption's SCOPE, in one place: the parent is 1, the whole command line is the
    house's shape, and the owner the pipe names is gone. A fourth question would be a wider
    exemption than the one that was recorded."""
    asked: list[tuple[str, int]] = []
    monkeypatch.setattr(load_reap, "_process", lambda pid: (1, EXACT_COMMAND))
    monkeypatch.setattr(reg, "pid_alive", lambda pid: asked.append(("owner", pid)) or False)
    assert load_reap.our_orphans([4242]) == [4242]
    assert asked == [("owner", 999999)], "the owner is asked about, and about nothing else"

    asked.clear()
    monkeypatch.setattr(load_reap, "_process", lambda pid: (7, EXACT_COMMAND))
    assert load_reap.our_orphans([4242]) == [], "a spinner with a live parent is not an orphan"
    assert asked == [], "a live parent is refused before the owner is asked about"

    asked.clear()
    monkeypatch.setattr(load_reap, "_process", lambda pid: (1, "/bin/sh -c sleep 300"))
    assert load_reap.our_orphans([4242]) == [], "another program's process is not ours"
    assert asked == [], "a process of another shape is refused before the owner is asked about"

    asked.clear()
    monkeypatch.setattr(load_reap, "_process", lambda pid: (1, EXACT_COMMAND))
    monkeypatch.setattr(reg, "pid_alive", lambda pid: asked.append(("owner", pid)) or True)
    assert load_reap.our_orphans([4242]) == [], "a spinner whose owner is alive is not ours"


def test_the_burning_filter_asks_four_questions_and_no_more(monkeypatch) -> None:
    """The second exemption's SCOPE, in one place: it is an orphan, it has burned, it is still
    burning, and the code a shell runs contains a busy loop. A fifth question would be a wider
    exemption than the one that was recorded -- and a DROPPED one is a narrower kill than the
    reason claims, which is the same defect in the other direction."""
    source = LIBRARY.read_text(encoding="utf-8")
    body = source[source.index("def burning_orphans(") : source.index("def reap_burning_orphans(")]
    for question in ("parent != 1", "cpu < min_cpu_s", "info[1] <= cpu", "has_busy_loop"):
        assert question in body, f"the filter stopped asking: {question}"
    # The floor is the house's, and it is a floor: a minute of CPU on an empty loop is a leak.
    assert load_reap.BURN_CPU_S == 60.0, load_reap.BURN_CPU_S
    assert "min_cpu_s: float = BURN_CPU_S" in source, "the floor is not the default"
