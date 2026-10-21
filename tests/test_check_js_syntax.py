"""`goh js-syntax`: the tracked JavaScript of a repository parses.

THE CLASS. A JavaScript source that does not parse is a defect nobody sees: the
file loads in a browser that never opens it, or in a web view that swallows
the error, and the tree stays green. The gate is structural -- it measures the
repository's own tracked sources, and it names the file and the line.

WHAT IS PROVEN HERE, in both directions, because a gate that cannot fail is not
a gate:

  * RED on a planted syntax error -- under `node --check` AND under the
    `JavaScriptCore` fallback that reads the same file with no node installed
    at all (the fallback is the engine a machine with nothing installed has,
    so it has to bite on its own).
  * RED on a template whose SUBSTITUTED form does not parse: a template is
    not JavaScript on its own, and the rule substitutes its placeholders with a
    neutral expression and parses THAT. A template whose skeleton is broken is
    a finding; a template whose placeholders are merely unfilled is not.
  * GREEN on a template that is not JavaScript on its own -- the half that
    proves the substitution happens, rather than the template being excluded
    from the check (an exclusion hides the subject, and the subject is the
    reason the gate exists).
  * GREEN on a clean tree.
  * RED when the floor is above the real count: a scan that stopped reading
    its subject reports clean, and the floor is what says so.
  * A REFUSAL, not a false red, on a source the engine cannot read (a module
    source under the fallback, which cannot parse one without running it).

The registry cites this file (`checks/gate_calibration.json`, key `js_syntax`),
so the estate's count of proven gates does not grow.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import commit_all, run_goh, write

# The shape the fallback engine exists to catch: a stray backtick leaves the
# template literal open to the end of the file.
STRAY = "const s = `unterminated;\nconsole.log(s);\n"
CLEAN = "const a = 1;\nconsole.log(a);\n"
# A template: its placeholder is filled in before it is parsed, so the file as
# it stands is NOT JavaScript -- and it passes.
TEMPLATE = "const timeout = ${TIMEOUT};\nconsole.log(timeout);\n"
# A template whose substituted form is still broken: `const timeout = 0 0;`.
BROKEN_TEMPLATE = "const timeout = ${A} ${B};\n"
# An ES module: `node --check` reads it, the fallback cannot.
MODULE = "import fs from 'node:fs';\nexport const x = 1;\n"

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc")


def _no_node_env() -> dict[str, str]:
    """An environment with no node on PATH: `/usr/bin` and `/bin` hold git, and node
    lives in homebrew's prefix on this machine."""
    return {**os.environ, "PATH": "/usr/bin:/bin"}


def _js_repo(repo: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        write(repo, rel, content)
    commit_all(repo, "javascript")
    return repo


def test_a_source_that_does_not_parse_is_red_and_named(repo: Path) -> None:
    _js_repo(repo, {"web/stray.js": STRAY})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/stray.js" in result.stdout, result.stdout
    assert "does not parse" in result.stdout, result.stdout


def test_a_clean_tree_is_green(repo: Path) -> None:
    _js_repo(repo, {"web/clean.js": CLEAN})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout, result.stdout


def test_the_scope_is_stated_not_implied(repo: Path) -> None:
    """An exclusion can hide the subject, so the scope is printed: the count, the
    shape of the paths, and every path the vendor list took out."""
    _js_repo(
        repo,
        {
            "web/a.js": CLEAN,
            "web/b.js": CLEAN,
            "node_modules/pkg/c.js": CLEAN,
        },
    )
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 tracked JavaScript file(s)" in result.stdout, result.stdout
    assert "shape: web (2)" in result.stdout, result.stdout
    assert "node_modules/pkg/c.js" in result.stdout, result.stdout


def test_a_template_is_judged_by_its_substituted_form(repo: Path) -> None:
    """The template as it stands is NOT JavaScript -- `const timeout = ${TIMEOUT};`
    does not parse -- so a gate that parsed it as it stands would refuse a file
    that is sound. The placeholder is filled in first."""
    _js_repo(repo, {"web/templates_page.js": TEMPLATE})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "substituted form" in result.stdout, result.stdout


def test_a_template_whose_substituted_form_does_not_parse_is_red(repo: Path) -> None:
    _js_repo(repo, {"web/templates_page.js": BROKEN_TEMPLATE})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/templates_page.js" in result.stdout, result.stdout
    assert "substituted form does not parse" in result.stdout, result.stdout


def test_a_malformed_placeholder_is_not_hidden_by_the_substitution(repo: Path) -> None:
    """`${a b}` is not a placeholder -- it is a hole that is not an expression --
    so the substitution leaves it alone and the parse still judges it."""
    _js_repo(repo, {"web/templates_page.js": "const t = `${a b}`;\n"})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/templates_page.js" in result.stdout, result.stdout


def test_a_floor_above_the_real_count_is_red(repo: Path) -> None:
    """The scope cannot shrink silently: a full-scope scan that reads less than
    the floor the repository declared is red, not clean."""
    _js_repo(repo, {"web/a.js": CLEAN})
    write(repo, ".gatesrc", "GOH_JS_SYNTAX=on\nGOH_JS_SYNTAX_MIN_FILES=5\n")
    commit_all(repo, "floor")
    result = run_goh(repo, "structural", "--full")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "GOH_JS_SYNTAX_MIN_FILES" in result.stdout + result.stderr, result.stdout


def test_a_floor_the_scope_meets_is_green(repo: Path) -> None:
    _js_repo(repo, {"web/a.js": CLEAN})
    write(repo, ".gatesrc", "GOH_JS_SYNTAX=on\nGOH_JS_SYNTAX_MIN_FILES=1\n")
    commit_all(repo, "floor")
    result = run_goh(repo, "structural", "--full")
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_repository_with_no_javascript_is_not_applicable(repo: Path) -> None:
    """Not a bare pass: the gate says what it looked for."""
    _js_repo(repo, {"README.md": "# nothing here\n"})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not applicable" in result.stdout, result.stdout


@pytest.mark.skipif(not JSC.exists(), reason="macOS's JavaScriptCore helper is not here")
def test_the_fallback_catches_the_same_defect_with_no_node(repo: Path) -> None:
    """The fallback exists for a machine with no node at all, so it has to bite on
    its own: the same stray backtick, read by JavaScriptCore, is red."""
    _js_repo(repo, {"web/stray.js": STRAY})
    result = run_goh(repo, "js-syntax", env=_no_node_env())
    assert result.returncode == 1, result.stdout + result.stderr
    assert "JavaScriptCore" in result.stdout, result.stdout
    assert "web/stray.js" in result.stdout, result.stdout


@pytest.mark.skipif(not JSC.exists(), reason="macOS's JavaScriptCore helper is not here")
def test_the_fallback_reads_a_module_source_as_unmeasured_not_as_a_defect(repo: Path) -> None:
    """JavaScriptCore can parse a module only by RUNNING it, and a gate does not
    run the repository's code. So the source is named as unmeasured -- a
    refusal -- and never as a syntax error it does not have."""
    _js_repo(repo, {"web/mod.mjs": MODULE})
    result = run_goh(repo, "js-syntax", env=_no_node_env())
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not measured" in result.stdout, result.stdout
    assert "web/mod.mjs" in result.stdout, result.stdout


def test_node_reads_a_module_source(repo: Path) -> None:
    _js_repo(repo, {"web/mod.mjs": MODULE})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
