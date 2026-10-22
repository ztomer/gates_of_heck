"""`goh js-syntax`: the tracked JavaScript of a repository parses.

THE CLASS. A JavaScript source that does not parse is a defect nobody sees: the
file loads in a browser that never opens it, or in a web view that swallows
the error, and the tree stays green. The gate is structural -- it measures the
repository's own tracked sources, and it names the file and the line.

WHAT IS PROVEN HERE, in both directions, because a gate that cannot fail is not
a gate:

  * RED on a planted syntax error -- under node AND under the `JavaScriptCore`
    fallback that reads the same file with no node installed at all (the
    fallback is the engine a machine with nothing installed has, so it has to
    bite on its own).
  * RED on a TOP-LEVEL `return` and on a TOP-LEVEL `await`, under both engines:
    the goal is the BROWSER'S classic script, and `node --check` -- which read
    the CommonJS goal -- accepted both. A gate that asks node the way node is
    usually asked is green on a file no page can load.
  * RED on a `.js` file that carries module syntax: a browser loads a `.js`
    file with `<script src>`, and a module is named `.mjs`.
  * GREEN on a `.mjs` file (a module, whatever it carries), with the report
    NAMING the goal it was judged with, and GREEN on a `.cjs` file with a
    top-level `return` (node's CommonJS, which no browser loads).
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
# A TOP-LEVEL `return`: valid CommonJS, and INVALID in a browser's classic
# script, which is what a `<script src>` is. `node --check` reads the CommonJS
# goal and ACCEPTS it, so a gate that judges the goal with `node --check` is
# green on a file the browser refuses to load -- and the page renders empty.
TOP_LEVEL_RETURN = "return 1;\n"
# A top-level `await`: the same shape, and legal in a MODULE only.
TOP_LEVEL_AWAIT = "const answer = await fetch('/answer');\n"

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


def test_a_top_level_return_is_red(repo: Path) -> None:
    """A browser's classic script REFUSES a top-level `return` -- the file never
    loads, and the page that loads it renders empty. `node --check` reads the
    CommonJS goal and accepts it, so the goal is the whole failure mode of an
    engine that parses a file the way node does."""
    _js_repo(repo, {"web/ret.js": TOP_LEVEL_RETURN})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/ret.js" in result.stdout, result.stdout


def test_a_top_level_await_is_red(repo: Path) -> None:
    """The same shape as the return: a top-level `await` is legal in a module
    and illegal in a classic script, so a classic source carrying one never
    loads."""
    _js_repo(repo, {"web/await.js": TOP_LEVEL_AWAIT})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/await.js" in result.stdout, result.stdout


def test_a_javascript_file_that_is_really_a_module_is_red(repo: Path) -> None:
    """A browser loads a `.js` file with `<script src>` -- a CLASSIC SCRIPT --
    and that goal refuses `import`. A module is named `.mjs`."""
    _js_repo(repo, {"web/eslint.config.js": MODULE})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/eslint.config.js" in result.stdout, result.stdout


def test_a_module_is_green_and_the_report_names_it(repo: Path) -> None:
    """A `.mjs` file is a module whatever it carries, and the report says so:
    the goal a source was judged with is a judgment a reader has to see."""
    _js_repo(repo, {"web/mod.mjs": MODULE})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "module 1 (web/mod.mjs)" in result.stdout, result.stdout


def test_a_commonjs_file_may_return_at_the_top_level(repo: Path) -> None:
    """A `.cjs` file is node's CommonJS, and no browser loads it -- so a
    top-level `return`, which the CommonJS goal allows, is green there."""
    _js_repo(repo, {"web/legacy.cjs": TOP_LEVEL_RETURN})
    result = run_goh(repo, "js-syntax")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CommonJS 1 (web/legacy.cjs)" in result.stdout, result.stdout


@pytest.mark.skipif(not JSC.exists(), reason="macOS's JavaScriptCore helper is not here")
def test_the_fallback_also_refuses_a_top_level_return(repo: Path) -> None:
    """The fallback is the engine a machine with no node has, so it has to reach
    the SAME goal: a top-level `return` is red under it too."""
    _js_repo(repo, {"web/ret.js": TOP_LEVEL_RETURN})
    result = run_goh(repo, "js-syntax", env=_no_node_env())
    assert result.returncode == 1, result.stdout + result.stderr
    assert "web/ret.js" in result.stdout, result.stdout


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
