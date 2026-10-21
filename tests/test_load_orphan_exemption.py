"""The RECORDED EXEMPTION: the one name lookup the house's kill-by-name rule allows.

THE RULE. A name is not an owner -- zinc's harness SIGKILLed another session's browser with a
`pkill -9 -f` in the middle of a reply (2026-09-23) -- so the house forbids killing by name
(`goh kill-by-name`), and an exemption is a RECORD: one file, one exact call, a reason, and a
stale entry fails.

THE EXEMPTION (2026-10-20). A load experiment's registry can VANISH: it lives in the experiment's
directory, and that directory is pruned, and the spinner is left burning a core with nothing
naming it -- eight of them, orphaned by a pytest that died, each about an hour of CPU. The
registry cannot reach those, so the backstop asks the ONE shape the house spawns: is this
process's command line EXACTLY `load_registry.SPINNER_COMMAND`, is its parent 1, and is the owner
its release pipe names gone? That question needs a name lookup, and the lookup and the kill sit
on ONE line, so the rule sees them instead of a rewrite hiding them.

WHAT IS PINNED HERE, IN BOTH DIRECTIONS.

  * the gate that guards the allowlist is GREEN with the entry, and RED without it: the
    exemption is load-bearing, and it is the house's own rule that says so;
  * a STALE entry -- the call changed, or is gone -- is RED, and so is a BROADENED call whose
    entry was not re-keyed: the rule sees both;
  * a BROADENED call whose entry WAS re-keyed is green to the rule -- the rule matches a call,
    not what a call does -- and RED here, because the entry must still name the narrow call, and
    the filter must still ask its three questions and no more;
  * the shape is matched WHOLE: a command line that starts the same way, or mentions the same
    words, is somebody else's process.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT, commit_all, run_goh, write

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402

ALLOW_FILE = REPO_ROOT / "kill_by_name_allow.json"
LIBRARY = REPO_ROOT / "lib" / "load_reap.py"

# The command names are assembled from parts, because this gate scans this file too: a fixture
# line naming the command is a kill by name to the gate (`tests/test_check_no_kill_by_name.py`
# does the same, and says why).
LOOKUP = "pg" + "rep"
KILL = "ki" + "ll"

# The command line of a spinner of the house's shape, and the owner its pipe names.
FIFO = "/tmp/goh-exempt/release-999999-0.fifo"
EXACT_COMMAND = f"/bin/sh -c {reg.spinner_command(FIFO)}"


def _exempted_call() -> str:
    """The call the exemption names: the one line in the library with the lookup and the kill.

    READ from the library, not written here: a copy in this file would be a second definition of
    the call, and it would drift the moment the call moved.
    """
    for line in LIBRARY.read_text(encoding="utf-8").splitlines():
        if LOOKUP + "(" in line and KILL + "(" in line:
            return line.strip()
    raise AssertionError("the library has no line with the lookup and the kill on it")


def _entries() -> list[dict]:
    data = json.loads(ALLOW_FILE.read_text(encoding="utf-8"))
    return list(data["entries"])


def _ours() -> list[dict]:
    return [entry for entry in _entries() if entry["path"] == "lib/load_reap.py"]


def _fixture(repo: Path, library: str, allow: str) -> None:
    """A scratch repository with one code file and one allowlist: what the rule reads."""
    write(repo, "lib/load_reap.py", library)
    write(repo, "kill_by_name_allow.json", allow)
    commit_all(repo)


def _allow(line: str) -> str:
    return json.dumps(
        {
            "entries": [
                {
                    "path": "lib/load_reap.py",
                    "line": line,
                    "status": "legitimate",
                    "reason": "the recorded exemption, for the test",
                }
            ]
        },
        indent=1,
    )


def test_the_exemption_is_one_entry_with_its_reason_written_down() -> None:
    """One file, one exact call, a reason: the shape of an exemption the house can read."""
    ours = _ours()
    assert len(ours) == 1, ours
    entry = ours[0]
    assert entry["line"] == _exempted_call(), entry
    assert entry["status"] == "legitimate", entry
    assert "shape" in entry["reason"], entry
    assert "our_orphans" in entry["reason"], entry
    assert len(entry["reason"]) > 80, "the reason says WHY, in words"


def test_the_exemption_still_matches_the_call_it_excuses() -> None:
    """A stale entry fails the house's rule. So the entry has to name the call that is IN the
    file: the one lookup, the one filter and the one kill, on one line."""
    source = LIBRARY.read_text(encoding="utf-8")
    assert _exempted_call() in source, "the exempted call is not the call in the file any more"
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
        and node.func.id == "our_orphans"
    ]
    assert len(lookups) == 1, lookups
    assert len(filters) == 1, filters
    assert lookups[0].lineno == filters[0].lineno, "the lookup and the filter are on one line"


def test_the_rule_is_green_with_the_exemption_and_red_without_it(repo: Path) -> None:
    """The exemption is load-bearing, and the house's own rule says so: the same file is green
    with the entry in `kill_by_name_allow.json` and red without it."""
    library = LIBRARY.read_text(encoding="utf-8")
    _fixture(repo, library, _allow(_exempted_call()))
    excused = run_goh(repo, "kill-by-name")
    assert excused.returncode == 0, excused.stdout + excused.stderr
    assert "allowlisted" in excused.stdout, excused.stdout

    _fixture(repo, library, json.dumps({"entries": []}))
    unexcused = run_goh(repo, "kill-by-name")
    assert unexcused.returncode == 1, "the rule does not see the lookup without the entry"
    assert f"a {LOOKUP} lookup feeds a {KILL}" in unexcused.stdout, unexcused.stdout


def test_a_stale_exemption_is_red(repo: Path) -> None:
    """The call changed and the entry did not: the rule sees a lookup nothing excuses, and an
    entry that matches no kill at all."""
    narrowed = _exempted_call()
    library = LIBRARY.read_text(encoding="utf-8").replace(
        narrowed,
        f"outcomes = [(pid, {KILL}(pid)) for pid in candidates]",
    )
    _fixture(repo, library, _allow(narrowed))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 1, "a stale entry is green: the ratchet does not bite"
    assert "stale" in result.stdout, result.stdout


def _broadened() -> str:
    """The same call with the shape filter taken out: the lookup feeds the kill DIRECTLY."""
    return f"outcomes = [(pid, {KILL}(pid)) for pid in {LOOKUP}(ORPHAN_PATTERN)]"


def test_a_broadened_call_without_a_re_key_is_red(repo: Path) -> None:
    """The lookup no longer goes through the house's shape, and the entry was left alone: the
    rule sees a lookup nothing excuses, and an entry that matches no kill at all."""
    narrowed = _exempted_call()
    library = LIBRARY.read_text(encoding="utf-8").replace(narrowed, _broadened())
    _fixture(repo, library, _allow(narrowed))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 1, "a broadened call is green: the ratchet does not bite"
    assert "stale" in result.stdout, result.stdout


def test_a_re_keyed_exemption_is_green_to_the_rule_and_caught_by_the_pin(repo: Path) -> None:
    """The rule's LIMIT, stated: it matches a CALL, not what a call does, so a broadened call
    whose entry was re-keyed is green to it. What catches that is the pin -- the entry has to
    name the NARROW call, and the narrow call has to be the one in the file."""
    narrowed = _exempted_call()
    broadened = _broadened()
    library = LIBRARY.read_text(encoding="utf-8").replace(narrowed, broadened)
    _fixture(repo, library, _allow(broadened))
    result = run_goh(repo, "kill-by-name")
    assert result.returncode == 0, "the rule matches the call it is given"
    # The pin, and the two facts it rests on. A broadened file with a re-keyed entry breaks both,
    # so the pin is RED where the rule is green.
    assert narrowed != broadened
    assert narrowed not in library, "the broadened file no longer carries the exempted call"
    assert _ours()[0]["line"] == narrowed, "the entry still names the narrow call"


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


def test_the_filter_asks_three_questions_and_no_more(monkeypatch) -> None:
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
