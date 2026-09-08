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
"""

EXCLUDED_MARKERS = (
    "/.build/",
    ".derived/",
    "/DerivedSources/",
    "/Tests/",
    "/tests/",
)


def is_code_under_test(path: str) -> bool:
    """True when a coverage record belongs in the denominator."""
    return not any(marker in path for marker in EXCLUDED_MARKERS)
