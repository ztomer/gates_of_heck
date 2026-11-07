"""What counts as "the code under test" in a Swift coverage measurement.

One definition, imported by both measurement paths. It lived only in
`checks/check_swift_coverage.py`; `gates/coverage_swift.py` measured the
same packages and applied no exclusion at all, so the two disagreed by more
than a factor of two on the same tree (antiknob, 2026-09-07: 4.78% vs
10.54%). Two copies of a rule is how they drift -- here there was one copy
and one absence, which is worse, because the second path looked like it
agreed and did not.

The rule itself, unchanged:

Generated sources. SwiftPM synthesises a test runner under `.build`; nobody
writes it and nobody can cover it deliberately.

Test sources. A test file is ~100% covered by definition -- it is the thing
doing the running -- so counting it means every test you add raises coverage
twice, once for the code it exercises and once for itself. Measured on a real
package (antiknob, 2026-09-07): 10.5% counting Tests/, 4.8% without. A floor
set on the first number can be met by writing tests that assert nothing,
which is precisely the gate this is supposed to be.

A path dependency's sources. `.package(path: "../Other")` compiles the other
package into the same test binary with coverage on, and its files carry no
marker the rule above can see: they are ordinary sources at an absolute path
outside the package. token-pool's widget (2026-11-07) read 39.3% over 17
files, 12 of them ZoneWM's SpaceBarKit -- the number measured the kit. So for
an SPM package the denominator is the package's OWN non-test targets, read
from `swift package describe --type json` (the manifest's answer, never a tree
walk), and a file outside every one of those target directories is not code
under test. A tree with no Package.swift (an Xcode project) has no such list
and keeps the marker rule alone.
"""

import json
import os
import subprocess

EXCLUDED_MARKERS = (
    "/.build/",
    ".derived/",
    "/DerivedSources/",
    "/Tests/",
    "/tests/",
)


class ScopeError(RuntimeError):
    """The package's own targets could not be read; the measurement has no scope."""


def own_source_roots(package_dir: str, describe=None):
    """Absolute source directories of the package's own non-test targets, or None.

    None means "not an SPM package" (no Package.swift): the marker rule alone
    applies. A package whose manifest cannot be described raises ScopeError --
    a coverage number with no known scope is not a number. `describe` is the
    seam: a callable (package_dir) -> JSON text, default `swift package describe`.
    """
    if not os.path.isfile(os.path.join(package_dir, "Package.swift")):
        return None
    if describe is None:
        describe = _swift_describe
    try:
        payload = json.loads(describe(package_dir))
    except (OSError, ValueError) as exc:
        raise ScopeError(f"swift package describe failed in {package_dir}: {exc}") from exc
    root = payload.get("path") or package_dir
    roots = [
        os.path.realpath(os.path.join(root, t["path"]))
        for t in payload.get("targets") or []
        if isinstance(t, dict) and t.get("type") != "test" and t.get("path")
    ]
    if not roots:
        raise ScopeError(f"{package_dir}/Package.swift describes no non-test target")
    return roots


def _swift_describe(package_dir: str) -> str:
    res = subprocess.run(
        ["swift", "package", "describe", "--type", "json"],
        cwd=package_dir,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=300,
    )
    if res.returncode != 0:
        raise OSError(f"exit {res.returncode}: {(res.stderr or res.stdout).strip()[:300]}")
    return res.stdout


def is_code_under_test(path: str, roots=None) -> bool:
    """True when a coverage record belongs in the denominator.

    `roots` (from own_source_roots) confines it to the package's own targets;
    None applies the marker rule alone.
    """
    if any(marker in path for marker in EXCLUDED_MARKERS):
        return False
    if roots is None:
        return True
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r + os.sep) for r in roots)
