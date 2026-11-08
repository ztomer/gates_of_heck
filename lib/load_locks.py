"""Does a command take a MACHINE-WIDE lock? -- the house's ONE answer, for `tools/load.py` and the
Bash guard that reads a `load.py` command line before it runs (GOH.LOADLOCK, 2026-11-08).

THE CLASS. `load.py --max 25m --oversubscribe 32 -- taskpolicy -b cargo test -q -p taxes-cli
--test app_check` starved cargo to 0.36 s of CPU in 21 minutes -- but cargo had FIRST taken
`~/.cargo/.package-cache`, and nine other agents' cargo processes across the machine blocked on
that lock for 15-20 minutes (sampled: the holder in read_manifest, the waiters in
CacheLocker::lock_exclusive). A load experiment whose command takes a machine-wide lock under
starvation is not an experiment on one process; it is a stall of every process that wants the lock.

So a load experiment refuses, before any spinner starts, a command that is a BUILD TOOL acquiring
shared locks. The way forward is to take the lock BEFORE the load and release it: build first
(`cargo test --no-run` prints the test binary), then put only the prebuilt binary under load.py.
The escape is `--shared-lock-ok <reason>`; an empty reason excuses nothing.

WHAT COUNTS (the real command, found through `lib/load_priority.operands`: taskpolicy, nice, env,
sudo, timeout, caffeinate, xcrun, ... and a shell's `-c` script):

  * cargo   build b test t run r check c clippy metadata bench nextest doc d fetch update fix
            install tree vendor rustc rustdoc package publish llvm-cov  (after `+toolchain` and
            the global flags `-Z x`, `--config x`, `-C dir`, `--color x`, `--locked` ...)
  * xcodebuild  anything but -version / -help / -usage / -showsdks
  * swift   build test run package  (also swift-build, swift-test, swift-run, swift-package)
  * gradle, gradlew  anything but --version / --help
  * go      build test run install vet get mod generate
  * npm     install i ci add update up      pnpm  install i add update up fetch
  * yarn    install add upgrade, and bare `yarn` (which installs)      bun  install i add update

`cargo fmt`, `cargo --version`, `npm run x`, `go version` and a prebuilt test binary take no such
lock and are left alone. A command name that is only an ARGUMENT (`./prebuilt cargo test`) is no
command at all.
"""

from __future__ import annotations

import load_priority

ESCAPE_FLAG = "--shared-lock-ok"
HELP = "allow a build tool that takes a machine-wide lock under load (a reason is required)"

CARGO = {
    "build", "b", "test", "t", "run", "r", "check", "c", "clippy", "metadata", "bench", "nextest",
    "doc", "d", "fetch", "update", "fix", "install", "tree", "vendor", "rustc", "rustdoc",
    "package", "publish", "llvm-cov",
}  # fmt: skip
CARGO_VALUE_FLAGS = {"-Z", "-C", "--config", "--color", "--manifest-path", "--target-dir"}
SWIFT = {"build", "test", "run", "package"}
GO = {"build", "test", "run", "install", "vet", "get", "mod", "generate"}
NPM = {"install", "i", "ci", "add", "update", "up"}
PNPM = {"install", "i", "add", "update", "up", "fetch"}
YARN = {"install", "add", "upgrade"}
BUN = {"install", "i", "add", "update"}
INFO_FLAGS = {"-version", "-help", "-usage", "-showsdks", "--version", "-v", "--help", "-h"}
HINTS = {
    "cargo": "`cargo test --no-run` (it prints the test binary) or `cargo build`",
    "xcodebuild": "`xcodebuild build-for-testing`, then the built test product",
    "swift": "`swift build --build-tests`, then the built `.build/debug/*PackageTests.xctest`",
    "gradle": "`./gradlew assemble`, then the built artifact",
    "go": "`go test -c -o x.test`, then `./x.test`",
    "node": "the install, then the program",
}


def _base(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _first_word(args: list[str], value_flags: set[str]) -> str | None:
    """The first non-flag word of `args` (a `+toolchain` is no subcommand), or None."""
    rest = list(args)
    while rest:
        tok = rest[0]
        if tok in value_flags:
            rest = rest[2:]
        elif tok.startswith(("-", "+")):
            rest = rest[1:]
        else:
            return tok
    return None


def _kind(argv: list[str]) -> tuple[str, str] | None:
    """(tool family, what was named) when `argv` is a lock-taking build command, else None."""
    if not argv:
        return None
    name, args = _base(argv[0]), argv[1:]
    if name == "cargo":
        word = _first_word(args, CARGO_VALUE_FLAGS)
        return ("cargo", f"cargo {word}") if word in CARGO else None
    if name in ("xcodebuild", "gradle", "gradlew"):
        if any(arg in INFO_FLAGS for arg in args):
            return None
        return ("xcodebuild" if name == "xcodebuild" else "gradle"), name
    if name == "swift":
        word = _first_word(args, set())
        return ("swift", f"swift {word}") if word in SWIFT else None
    if name in ("swift-build", "swift-test", "swift-run", "swift-package"):
        return "swift", name
    if name == "go":
        word = _first_word(args, set())
        return ("go", f"go {word}") if word in GO else None
    families = {"npm": NPM, "pnpm": PNPM, "yarn": YARN, "bun": BUN}
    if name in families:
        word = _first_word(args, set())
        if word in families[name] or (name == "yarn" and word is None and not args):
            return "node", f"{name} {word or ''}".strip()
    return None


def locking_command(argv: list[str]) -> tuple[str, str] | None:
    """(family, command) of the lock-taking build command `argv` runs (("cargo", "cargo test")),
    looked for through its wrappers, or None when it runs nothing that takes a machine-wide
    lock."""
    for real in load_priority.operands(argv):
        found = _kind(real)
        if found:
            return found
    return None


def refusal(count: int, found: tuple[str, str]) -> str:
    """Why a load experiment over a lock-taking build command is refused, and the way out."""
    family, named = found
    return (
        f"the command is a build tool that takes a machine-wide lock ({named}): started under "
        f"{count} spinner(s) it is starved, and it HOLDS that lock the whole time -- every other "
        "process on the machine that wants it waits too. That is how `load.py 32 -- taskpolicy -b "
        "cargo test` held ~/.cargo/.package-cache for 21 minutes and stalled nine other agents' "
        "cargo runs (2026-11-08). Build first -- "
        f"{HINTS[family]} -- and put only the prebuilt binary under load.py; "
        f"if the lock is yours alone, pass `{ESCAPE_FLAG} <reason>`."
    )


def preflight(count: int, command: list[str], reason: str | None) -> str | None:
    """`tools/load.py`'s refusal for an experiment over `command`, or None when it may run."""
    if reason is not None and not reason.strip():
        return (
            f"{ESCAPE_FLAG} with no reason excuses nothing: name why the lock this command takes "
            "harms no one"
        )
    found = locking_command(command)
    if count > 0 and found and reason is None:
        return refusal(count, found)
    return None


def judge_invocation(tokens: list[str]) -> str | None:
    """The refusal for a `load.py` argv (what follows the script), or None."""
    escapes, count, command = load_priority.split_invocation(tokens)
    if escapes.get(ESCAPE_FLAG) or not count or count <= 0 or not command:
        return None
    found = locking_command(command)
    return refusal(count, found) if found else None


def refusals(
    count: int, command: list[str], background_ok: str | None, lock_ok: str | None
) -> list[str]:
    """EVERY reason a load experiment over `command` is refused before it starts (priority and
    shared lock): fixing one refusal only to meet the next is a second round trip."""
    found = (
        load_priority.preflight(count, command, background_ok),
        preflight(count, command, lock_ok),
    )
    return [why for why in found if why]
