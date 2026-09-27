#!/usr/bin/env python3
"""check_swift_warnings.py — one SwiftPM build, and every compiler warning in the repo's own sources fails it.

For a repo that judges warnings from build output rather than building with
`-warnings-as-errors` (swift_gate.sh's way): because its dependencies warn, or because a second
build configuration would cost a full rebuild on every alternation.

WHY THIS EXISTS. Two gates split the job and neither could do it. `check-concurrency` built first
and matched only the diagnostic groups that trap at runtime, spelled `#SendableClosureCaptures` in
its grep; `swift build` colours its output even into a pipe and wraps each group name in a terminal
hyperlink, so the name never appears whole and that grep never matched anything. `check-deprecated`
matched every warning, but ran its own `swift build` second, which on the cold pre-push tree has
nothing left to compile and prints no warnings at all. So "every warning fatal" was never true at
push: found in ZoneWM 2026-09-26, when a `self` captured in a `@Sendable` closure in a test, and an
unneeded `nonisolated(unsafe)` in the agent, both passed it.

THE RULE. One `swift build --build-tests`; its whole output is read once, with every ANSI colour and
OSC hyperlink stripped first. Any `warning:` whose file is under this repo's Sources/ or Tests/ fails
(dependency warnings are exempt). Warnings in the groups that are runtime traps under Swift 6
dynamic isolation are named as such. A build that fails fails the gate: files it never compiled
printed no warnings. On a warm tree only what was recompiled is seen; run it cold where it counts.

  check_swift_warnings.py                      # build (in the git repo at cwd) and judge
  check_swift_warnings.py --dirs Sources,Tests # which top-level dirs are "ours" (the default)
  check_swift_warnings.py --log FILE           # judge a saved build log
  check_swift_warnings.py --selftest           # prove the parser catches the real byte formats
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile

DEFAULT_DIRS = "Sources,Tests"
LOG_NAME = "swift-build-warnings.log"
# The diagnostic groups that are runtime traps under Swift 6 dynamic actor isolation (D-0016).
TRAP_GROUPS = {"ActorIsolatedCall", "SendableClosureCaptures", "SendableFunctionConversion",
               "ConformanceIsolation", "SendingRisksDataRace"}

OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")   # hyperlinks: ESC ] ... (BEL | ESC \)
CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")                # colours: ESC [ ... m
WARNING = re.compile(r"^(?P<path>/[^:]+):(?P<line>\d+):(?P<col>\d+): warning: (?P<msg>.*)$")
GROUP = re.compile(r"\[#(?P<group>\w+)\]\s*$")


def plain(text):
    return CSI.sub("", OSC.sub("", text))


def repo_root():
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    return os.path.realpath(top.stdout.strip() if top.returncode == 0 else os.getcwd())


def ours(path, repo, dirs):
    real = os.path.realpath(path)
    return any(real.startswith(os.path.join(repo, d) + os.sep) for d in dirs)


def findings(output, repo, dirs=tuple(DEFAULT_DIRS.split(","))):
    """Every warning in our sources, once each: (location, message, group or None)."""
    seen = {}
    for raw in output.splitlines():
        m = WARNING.match(plain(raw).strip())
        if not m or not ours(m.group("path"), repo, dirs):
            continue
        g = GROUP.search(m.group("msg"))
        where = f"{os.path.relpath(m.group('path'), repo)}:{m.group('line')}:{m.group('col')}"
        seen[where + m.group("msg")] = (where, m.group("msg"), g.group("group") if g else None)
    return sorted(seen.values())


def selftest():
    repo = "/r"
    esc = "\x1b"
    hyperlinked = (f"/r/Tests/T.swift:19:26: {esc}[1;33mwarning: {esc}[1;39mcapture of 'self' in a "
                   f"'@Sendable' closure [#{esc}]8;;https://docs.swift.org/x{esc}\\SendableClosureCaptures"
                   f"{esc}]8;;{esc}\\]{esc}[0;0m")
    coloured = (f"/r/Sources/A.swift:60:13: {esc}[1;33mwarning: {esc}[1;39m'nonisolated(unsafe)' is "
                f"unnecessary{esc}[0;0m")
    plain_dep = "/r/Sources/B.swift:4:12: warning: 'old()' is deprecated [#DeprecatedDeclaration]"
    dependency = "/r/.build/checkouts/Dep/X.swift:1:1: warning: something in a dependency"
    elsewhere = "/elsewhere/Sources/C.swift:1:1: warning: not this repo"
    note = f"   {esc}[0;36m|{esc}[0;0m  `- {esc}[1;33mwarning: {esc}[1;39mrepeated in the snippet{esc}[0;0m"
    got = findings("\n".join([hyperlinked, coloured, plain_dep, dependency, elsewhere, note]), repo, ("Sources", "Tests"))
    expected = [("Sources/A.swift:60:13", "'nonisolated(unsafe)' is unnecessary", None),
                ("Sources/B.swift:4:12", "'old()' is deprecated [#DeprecatedDeclaration]", "DeprecatedDeclaration"),
                ("Tests/T.swift:19:26", "capture of 'self' in a '@Sendable' closure [#SendableClosureCaptures]",
                 "SendableClosureCaptures")]
    if got != expected:
        print("✗ check_swift_warnings selftest: the parser misread the build's byte formats")
        for row in got:
            print(f"  got      {row}")
        for row in expected:
            print(f"  expected {row}")
        return 1
    print("✓ check_swift_warnings selftest: hyperlinked, coloured and plain warnings are caught; dependencies are not")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--log", help="judge a saved build log instead of building")
    parser.add_argument("--dirs", default=DEFAULT_DIRS, help="comma-separated top-level dirs that are ours")
    parser.add_argument("--selftest", action="store_true")
    opts = parser.parse_args()
    if opts.selftest:
        return selftest()
    repo = repo_root()
    dirs = tuple(d for d in opts.dirs.split(",") if d)
    log = opts.log or os.path.join(tempfile.gettempdir(), f"{os.path.basename(repo)}-{LOG_NAME}")
    if opts.log:
        with open(opts.log, encoding="utf-8", errors="replace") as f:
            output, status = f.read(), 0
    else:
        done = subprocess.run(["swift", "build", "--build-tests"], cwd=repo, capture_output=True,
                              text=True, errors="replace", check=False)
        output, status = done.stdout + done.stderr, done.returncode
        with open(log, "w", encoding="utf-8") as f:
            f.write(output)
    if status != 0:
        print(f"✗ the build failed (exit {status}); files it never compiled printed no warnings. Log: {log}")
        return 1
    found = findings(output, repo, dirs)
    traps = [f for f in found if f[2] in TRAP_GROUPS]
    rest = [f for f in found if f[2] not in TRAP_GROUPS]
    if traps:
        print("✗ concurrency warnings (each is a potential runtime trap under Swift 6 dynamic isolation):")
        for where, msg, _ in traps:
            print(f"  {where}: {msg}")
        print("  fix the isolation (D-0016) — never silence.")
    if rest:
        print("✗ compiler warnings in our sources (dependency warnings are exempt):")
        for where, msg, _ in rest:
            print(f"  {where}: {msg}")
        print("  deprecated API? migrate to the replacement — never silence the warning.")
    if found:
        return 1
    print(f"✓ no compiler warnings in {', '.join(dirs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
