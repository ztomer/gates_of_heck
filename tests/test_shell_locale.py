"""bash never sets a locale variable itself; a command gets one via `env` (GOH.PROVENBUILD).

THE CRASH (v0.122.0's release, 2026-11-15). The release gate's coverage step failed
tests/test_proven_made.py::test_a_build_into_a_directory_an_earlier_step_made_is_its_product_too:
a push export skipped the build its consumer needed. The measurement of what the build made was
empty, because the subshell measuring it had DIED -- silently, after its first command:

    { LC_ALL=C comm -13 "$1" <(...); <find what the step wrote>; } | sed ... | LC_ALL=C sort -u

A temporary `LC_ALL=C` on a command that is not the last of a forked bash subshell is disposed of
IN that subshell, and bash re-reads its locale (`dispose_temporary_env` -> `sv_locale` ->
`libintl_setlocale`). Homebrew's bash links gettext's libintl, which with no LANG set asks
CoreFoundation for the user's preferred languages -- in a process that forked without exec. macOS
reports it as "*** single-threaded process forked ***", "crashed on child side of fork pre-exec":
SIGSEGV in `_os_log_preferences_refresh` (~/Library/Logs/DiagnosticReports/bash-*.ips, 15 of that
signature on 2026-11-15 alone). Whether a fork crashes depends on CoreFoundation's cached state in
the parent, so it is load- and timing-dependent: 2-4 of 48 runs of that test under `-n 12`, never
alone. bash's EXIT path runs on the SIGSEGV with $? still 0, so nothing reads as a failure.

THE RULE. A shell this repo ships never assigns LC_*/LANG/LANGUAGE in bash -- no temporary
prefix, no export/local/plain assignment, no unset -- since any of them can run in a forked
subshell (a pipeline element, `( )`, `$( )`, `<( )`, a function called from one).
`env LC_ALL=C sort` sets it in the child `env` execs; bash's own locale never moves, so the
crash is unreachable.
"""

from __future__ import annotations

import re

from _own_files import own_source_files
from conftest import REPO_ROOT

# A shell is chosen by its shebang, never its extension: `lib/goh-rustc` (`#!/bin/sh`, no `.sh`)
# went unread while the selection was bash5's (GOH.NITS3). macOS's /bin/sh IS bash, so a `sh`
# shebang carries the same fork-time locale crash.
SHEBANG = re.compile(r"^#!\s*(?:/usr)?/bin/(?:env\s+(?:-S\s+)?)?(?:\S*/)?(?:ba|z|k|da)?sh(?:\s|$)")


def _is_shell(f: str) -> bool:
    if f.endswith((".sh", ".bash")):
        return True
    with open(REPO_ROOT / f, "rb") as fh:
        first = fh.readline(200).decode("utf-8", "replace")
    return bool(SHEBANG.match(first))


def shell_files() -> list[str]:
    return [f for f in own_source_files() if _is_shell(f)]


LOCALE = re.compile(r"(?<![\w$-])(?:LC_[A-Z]+|LANG|LANGUAGE)=")
UNSET = re.compile(r"(?:^|[\s;&|({])unset\s+(?:-v\s+)?[^;&|]*\b(?:LC_[A-Z]+|LANG|LANGUAGE)\b")
# The command a match belongs to starts after the last of these on its line.
SEPARATOR = re.compile(r"\$\(|<\(|>\(|&&|\|\||[|;&(){}`]")
ENV = re.compile(r"^\s*env(?:\s+-\S+(?:\s+[A-Za-z_]\w*(?![\w=]))?)*\s+(?:[A-Za-z_]\w*=\S*\s+)*$")


def _code(line: str) -> str:
    """The line without a trailing comment (a `#` at the start or after whitespace)."""
    m = re.search(r"(?:^|\s)#", line)
    return line[: m.start()] if m else line


def findings(text: str) -> list[tuple[int, str]]:
    """(line, text) of each locale variable bash itself would set or unset."""
    out = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = _code(raw)
        hit = bool(UNSET.search(line))
        for m in LOCALE.finditer(line):
            head = SEPARATOR.split(line[: m.start()])[-1]
            if not ENV.match(head):
                hit = True
        if hit:
            out.append((i, raw.strip()))
    return out


def test_the_check_sees_the_shape_that_crashed():
    crashed = '{\n    LC_ALL=C comm -13 "$1" <(printf \'%s\\n\' "$now")\n} | LC_ALL=C sort -u\n'
    assert [n for n, _ in findings(crashed)] == [2, 3]
    assert findings(crashed.replace("LC_ALL=C", "env LC_ALL=C")) == []
    for bad in ("export LANG=C", "local LC_ALL=C", "LC_COLLATE=C", "unset LC_ALL",
                "x=$(LC_ALL=C sort)", "a | LANGUAGE=en tr a b"):  # fmt: skip
        assert findings(bad) != [], bad
    for ok in ("env -u LANG LC_ALL=C sort", "env LC_ALL=C GOH_X=1 sort", "# LC_ALL=C sort",
               'echo "$LC_ALL"', "GOH_LANG=x run"):  # fmt: skip
        assert findings(ok) == [], ok


def test_the_selection_reads_every_shell_by_its_shebang():
    files = shell_files()
    for f in ("lib/goh-rustc", "hooks/pre-push", "lib/hookgit/git"):
        assert f in files, f
    for line in ("#!/bin/sh", "#!/usr/bin/env bash", "#!/usr/bin/env -S bash -eu", "#!/bin/zsh"):
        assert SHEBANG.match(line), line
    for line in ("#!/usr/bin/env python3", "#!/usr/bin/env ruby", "#!/bin/shx", "# sh"):
        assert not SHEBANG.match(line), line


def test_no_shell_of_this_repo_sets_a_locale_in_bash():
    bad = [
        f"{f}:{i}: {line}"
        for f in shell_files()
        for i, line in findings((REPO_ROOT / f).read_text(encoding="utf-8", errors="replace"))
    ]
    assert not bad, (
        "bash sets a locale variable itself -- in a forked subshell that crashes on macOS "
        "(libintl -> CoreFoundation after fork); write `env LC_ALL=C cmd`:\n" + "\n".join(bad)
    )
