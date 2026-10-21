"""The JavaScript cases of the shared-gate canary, split out of `_canary_cases.py`.

WHY A SPLIT, and why this file. The table in `_canary_cases.py` is the house's list of the gates
that can fail, one case each, and it had reached the 500-line cap -- a hard rule ("split, never
exempt"). The JavaScript cases are their own group (the parse gate, `goh js-syntax`: a stray
backtick, and a template whose substituted form does not parse), so they live here rather than
pushing the table over.

THE LIST IS STILL ONE LIST. `_canary_cases.py` splices these in, so every reader -- this repo's
`tests/test_canary.py` and game_asset_factory's `tools/check_gate_canary.py` -- sees one table and
does not know a file was split. A case is (label, gate script, argv, violating tree, clean tree),
and it is worthless unless BOTH directions hold.
"""

# (label, script, argv, {path: violating content}, {path: clean content})
CASES = [
    # The violating tree is the shape the fallback engine exists to catch: a stray
    # backtick, which leaves the template literal open to the end of the file. The
    # clean tree is JavaScript that parses.
    (
        "a tracked JavaScript source that does not parse is refused",
        "check_js_syntax.py",
        [],
        {"web/stray.js": "const s = `unterminated;\nconsole.log(s);\n"},
        {"web/clean.js": "const a = 1;\nconsole.log(a);\n"},
    ),
    # The template half: a template is judged by its SUBSTITUTED form, so the clean
    # tree here is a file that is NOT JavaScript on its own -- its placeholder is
    # filled in before it is parsed -- and it passes. The violating tree's
    # substituted form still does not parse.
    (
        "a template whose substituted form does not parse is refused",
        "check_js_syntax.py",
        [],
        {"web/templates_page.js": "const timeout = ${A} ${B};\n"},
        {"web/templates_page.js": "const timeout = ${TIMEOUT};\nconsole.log(timeout);\n"},
    ),
]
