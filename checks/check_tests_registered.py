#!/usr/bin/env python3
"""Every test source on disk must be registered with its runner.

Two sibling test files (~1200 lines) were silently dropped from a CMake build
and nobody noticed, because nothing compared the set of test files on disk
against the set the build system actually compiles. The tests advertised
coverage that never ran. This gate closes that hole: it is the file-set vs
registration-set diff, generalized across build systems.

CMake mode (--buildsystem cmake) is implemented: every test source under
--tests-dir must appear as a source token INSIDE an add_executable(...) or
add_test(...) block in --makefile. A mention elsewhere (a comment, a set()
that feeds nothing, prose) does not register anything — the ancestor gate
matched the path anywhere in the file, so a stale comment could stand in for
a real target. Known limit, stated: sources collected into a variable and
splatted later (`add_executable(t ${TEST_SRCS})`) cannot be resolved by a
static read; list test sources explicitly or use the opt-out below.

A deliberately-unregistered test file may carry, anywhere in its first lines,
`# goh-unregistered-ok: <reason>` (or //) — reviewed like code, say WHY.

Other build systems are TODO stubs that exit 2 with a NAMED reason rather
than pretending to have checked (a fake OK here is exactly the silent-drop it
exists to catch).

    check_tests_registered.py --buildsystem cmake
    check_tests_registered.py --buildsystem cmake --tests-dir tests --makefile CMakeLists.txt

Exit codes: 0 all registered · 1 orphaned test files · 2 usage/config error.
"""

import argparse
import os
import re
import sys

TEST_FILE_RE = re.compile(r"^(test_.+|.+_test)\.(cpp|cc|cxx|c|m|mm|rs|py|swift)$")
OPT_OUT_RE = re.compile(r"(?:^|\n)\s*(?:#|//)\s*goh-unregistered-ok:\s*(\S[^\n]*)")

# Stubs MUST name why they are stubs — an empty reason would be pretending.
UNSUPPORTED = {
    "xcode": "no static mapping of test files to targets; audit the .xcodeproj instead",
    "swift-pm": "SPM registers every Tests/ file implicitly — nothing to compare against",
    "bazel": "needs BUILD.bazel target parsing; not implemented",
    "meson": "needs meson.build target parsing; not implemented",
}

TARGET_BLOCK = re.compile(r"\b(?:add_executable|add_test)\s*\(")


def cmake_registered_tokens(text: str) -> set:
    """Source tokens named inside add_executable/add_test blocks (paren-balanced)."""
    tokens = set()
    for m in TARGET_BLOCK.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        body = text[m.end() : i - 1]
        for tok in body.split():
            tok = tok.strip('"')
            tok = re.sub(r"^\$\{CMAKE_CURRENT_SOURCE_DIR\}/", "", tok)
            tokens.add(tok)
            tokens.add(os.path.basename(tok))
    return tokens


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buildsystem", required=True, choices=sorted(UNSUPPORTED) + ["cmake"])
    ap.add_argument("--tests-dir", default="tests")
    ap.add_argument("--makefile", default="CMakeLists.txt")
    args = ap.parse_args()

    if args.buildsystem != "cmake":
        print(
            f"✗ [test_registration] --buildsystem {args.buildsystem} is not "
            f"implemented yet: {UNSUPPORTED[args.buildsystem]}",
            file=sys.stderr,
        )
        return 2

    root = os.getcwd()
    makefile = os.path.join(root, args.makefile)
    if not os.path.isfile(makefile):
        print(
            f"✗ [test_registration] {args.makefile} not found under {root} — "
            "pass --makefile or run from the repo root",
            file=sys.stderr,
        )
        return 2
    tests_dir = os.path.join(root, args.tests_dir)
    if not os.path.isdir(tests_dir):
        print(
            f"✗ [test_registration] tests dir {args.tests_dir}/ not found under {root}",
            file=sys.stderr,
        )
        return 2

    with open(makefile, encoding="utf-8", errors="replace") as fh:
        registered = cmake_registered_tokens(fh.read())

    orphans = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not TEST_FILE_RE.match(name):
                continue
            total += 1
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if name in registered or rel in registered:
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8",
                      errors="replace") as fh:
                if OPT_OUT_RE.search(fh.read(2000)):
                    continue  # deliberate, with a reason on file
            orphans.append(rel)

    if orphans:
        print(
            f"✗ [test_registration] {len(orphans)} test file(s) exist under "
            f"{args.tests_dir}/ but are NOT registered in {args.makefile} — "
            "they compile into no target and never run:",
            file=sys.stderr,
        )
        for rel in orphans:
            print(f"    {rel}", file=sys.stderr)
        print(
            "\n  Add them to an add_executable/add_test block, fix whatever\n"
            "  made them unbuildable, delete them, or mark them in-file with\n"
            "  `goh-unregistered-ok: <reason>`.",
            file=sys.stderr,
        )
        return 1

    print(
        f"→ [test_registration] OK — all {total} test file(s) under "
        f"{args.tests_dir}/ are registered in {args.makefile}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
