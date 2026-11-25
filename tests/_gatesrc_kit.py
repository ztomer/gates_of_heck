"""A fixture's `.gatesrc`, written the one way: it declares its roadmap (GOH.ROADMAPHOOK).

Every gated repo names its roadmap in `.gatesrc` (`GOH_ROADMAP=<file>`, or `none`), and an unset
one is red (GOH.ROADMAPREQ). A test seam, GOH_ROADMAP_PENDING, once excused every fixture from it,
so a fixture judged a repo no consumer could be. It is gone: a fixture that writes a `.gatesrc`
writes it here, and one that says nothing about its roadmap declares `none` (no roadmap, so a plan
file in it is a finding). A body that names GOH_ROADMAP itself is kept as written.

    write_gatesrc(repo, "GOH_MAX_LINES=10\\n")      # the file, declared
    declared("GOH_MAX_LINES=10\\n")                 # the text, for a fixture that writes it itself
"""

from __future__ import annotations

import re
from pathlib import Path

ROADMAP_NONE = "GOH_ROADMAP=none\n"
_DECLARES = re.compile(r"^\s*(?:export\s+)?GOH_ROADMAP=", re.MULTILINE)


# A fixture gate.sh line: the mark gates/structural.sh leaves for the scope ($1) and tree judged,
# with nothing judged. A commit or push gate finding no mark runs the structural layer itself,
# after the gate (2 to 4 s a commit or push); a test of anything else says it was run.
# tests/test_commit_gate_structural.py is the test of the mark and of what runs without it.
STRUCTURAL_RAN = (
    '[ -z "${GOH_STRUCTURAL_MARK:-}" ] || '
    'printf \'%s\\t%s\\n\' "$1" "$(git rev-parse --show-toplevel)" >>"$GOH_STRUCTURAL_MARK"\n'
)


def declared(body: str = "") -> str:
    """`body` with a roadmap declaration: as written when it has one, else with `none` appended."""
    if _DECLARES.search(body):
        return body
    return body + ("\n" if body and not body.endswith("\n") else "") + ROADMAP_NONE


def declared_bytes(body: bytes = b"") -> bytes:
    """`declared` for a fixture that holds its files as bytes."""
    return declared(body.decode("utf-8")).encode("utf-8")


def write_gatesrc(repo: Path, body: str = "", encoding: str = "utf-8") -> Path:
    """Write `repo/.gatesrc` from `body`, declared; the path written."""
    path = Path(repo) / ".gatesrc"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(declared(body), encoding=encoding)
    return path


def write_message_hook(repo: Path, body: str = "") -> None:
    """`repo` with goh's stock commit-msg as its ONLY hook, and a `.gatesrc` of `body`.

    What install.sh leaves for a message check, less the pre-commit: a test of the message layer
    committed through the whole structural gate too, 2 to 3 s a commit. install.sh writing the hook
    is tests/test_install_atomic.py's; the pre-commit's gate tests/test_install.py's."""
    hook = Path(repo) / ".githooks" / "commit-msg"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes((Path(__file__).resolve().parents[1] / "hooks" / "commit-msg").read_bytes())
    hook.chmod(0o755)
    with (Path(repo) / ".git" / "config").open("a", encoding="utf-8") as config:
        config.write("[core]\n\thooksPath = .githooks\n")
    write_gatesrc(repo, body)


def as_fixture(rel: str, content: str | bytes) -> str | bytes:
    """`content` as a fixture writes `rel`: declared when `rel` is the repo's `.gatesrc`, else as
    given. For the fixtures that hold their files as a {rel: content} dict."""
    if rel != ".gatesrc":
        return content
    return declared_bytes(content) if isinstance(content, bytes) else declared(content)
