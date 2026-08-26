"""coverage_gate.sh — the C++ percentage must be LINE coverage, by name.

Split out of test_coverage_gate.py at the repo's own 500-line cap. These pin
ONE contract: the number the cpp path compares against --floor is the line
percent, and it is never selected by column position.
"""

import subprocess

from conftest import REPO_ROOT

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"


# ── the cpp percentage is LINE coverage, by name ────────────────────────────
#
# The cpp path read the LAST column of `llvm-cov report`'s TOTAL row
# (`awk '{print $NF}'`) and called it the line percent. That row is
# Regions | Functions | Lines | Branches, so the last column is BRANCH
# coverage -- structurally lower than line coverage, so every C++ repo was
# graded against a stricter metric than --floor documents. Measured on one
# repo: 60.80% where lines were 71.21%.
#
# It survived because the column count is NOT fixed: a build instrumented
# without branch coverage has no branch columns, and there $NF really is the
# line percent. Right on some builds, silently wrong on others.


def test_cpp_reads_the_named_lines_total_not_the_last_column():
    """The parse must not be positional at all."""
    body = COV_GATE.read_text()
    start = body.index("run_cpp()")
    end = body.index("run_py()")
    # CODE only. The comment above the parse names the old bug on purpose, and
    # a test that cannot tell prose from an expression would forbid explaining
    # it.
    cpp = "\n".join(
        ln for ln in body[start:end].splitlines() if not ln.lstrip().startswith("#")
    )

    assert "$NF" not in cpp, (
        "the cpp coverage percent is parsed positionally again -- the TOTAL "
        "row's last column is BRANCH coverage on a branch-instrumented build"
    )
    assert "-summary-only" in cpp, "expected the JSON summary export"
    assert '["totals"]["lines"]["percent"]' in cpp, (
        "the line percent must be selected BY NAME, so it cannot drift with "
        "the report's column layout"
    )


def test_cpp_line_percent_extraction_picks_lines_out_of_a_real_summary():
    """The extractor, run against llvm-cov's actual JSON shape.

    Every metric is given a DIFFERENT value so a wrong pick is unambiguous --
    reading branches instead of lines returns 60.80, not something close.
    """
    import json

    body = COV_GATE.read_text()
    start = body.index("import json,sys")
    end = body.index("pass'", start)
    snippet = body[start:end] + "pass"

    summary = {
        "data": [
            {
                "totals": {
                    "regions": {"percent": 69.84},
                    "functions": {"percent": 78.63},
                    "lines": {"percent": 71.21},
                    "branches": {"percent": 60.80},
                }
            }
        ]
    }
    r = subprocess.run(
        ["python3", "-c", snippet],
        input=json.dumps(summary), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "71.21", (
        f"expected the LINES percent, got {r.stdout.strip()!r} "
        "(60.80 would mean branches, 69.84 regions, 78.63 functions)"
    )


def test_cpp_line_percent_extraction_stays_quiet_on_junk():
    """Malformed input must produce no number, so the caller's own guard
    reports a parse failure instead of a bogus percentage sailing through."""
    body = COV_GATE.read_text()
    start = body.index("import json,sys")
    end = body.index("pass'", start)
    snippet = body[start:end] + "pass"

    for junk in ("", "not json", '{"data": []}', '{"data": [{"totals": {}}]}'):
        r = subprocess.run(
            ["python3", "-c", snippet],
            input=junk, capture_output=True, text=True,
        )
        assert r.stdout.strip() == "", f"{junk!r} produced {r.stdout!r}"
