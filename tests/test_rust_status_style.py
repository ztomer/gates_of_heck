"""A Rust status line takes its glyph from `goh-tui`, which reads `tui/stylerc` (BACKLOG 4.14).

The shell and Python halves of the house TUI read the stylerc; a Rust binary had nothing to read it
with, so goh spelled its own `✓`/`✗`/`⚠`/`→`/`·` in some 400 string literals. They matched the
stylerc only because nobody had changed it. This test is the gate that keeps the class closed: a
string literal in shipped Rust source that OPENS a line with a Kare status glyph and a space -- at
its start, or right after a `\\n` -- is a hand-spelled status line. Write `{OK}` (and `START`,
`STEP`, `ERR`, `WARN`) from `goh_tui` instead. Test modules are exempt: they state the output the
current stylerc produces.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

GLYPH_LINE = re.compile(r"(?:^|\\n)[→·✓✗⚠] ")
LITERAL = re.compile(r'(?<![r#\w])"((?:\\.|[^"\\])*)"', re.S)
TEST_MODULE = re.compile(r"^#\[cfg\(test\)\]\s*\n\s*mod ", re.M)
THE_CRATE = "crates/goh-tui/"


def shipped(text: str) -> str:
    """The source without its inline test modules, its line comments blanked.

    Only the module's own braces are cut: shipped code after a test module is still shipped, and
    reading "everything above the first test module" let a status line below one pass unread.
    Format strings balance their braces (`{OK}`, `{{`), so counting them finds the module's end.
    """
    while cut := TEST_MODULE.search(text):
        head = re.match(r"\w+\s*([;{])", text[cut.end() :])
        if head is None or head.group(1) == ";":  # `mod tests;`: the module lives in its own file
            text = text[: cut.start()] + text[cut.end() :]
            continue
        depth, i = 0, cut.end() + head.end() - 1
        while i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                break
            i += 1
        text = text[: cut.start()] + text[i + 1 :]
    return re.sub(r"(?m)^\s*//.*$", "", text)


def hand_spelled(text: str) -> list[str]:
    return [
        m.group(0)[:60] for m in LITERAL.finditer(shipped(text)) if GLYPH_LINE.search(m.group(1))
    ]


def test_no_shipped_rust_spells_a_status_glyph() -> None:
    found = {}
    for path in (REPO_ROOT / "crates").rglob("*.rs"):
        rel = str(path.relative_to(REPO_ROOT))
        if "target" in path.parts or rel.startswith(THE_CRATE) or path.name == "tests.rs":
            continue
        if "/tests/" in rel:
            continue
        hits = hand_spelled(path.read_text(encoding="utf-8"))
        if hits:
            found[rel] = hits
    assert found == {}, f"print through goh_tui's glyphs: {found}"


def test_the_scan_sees_each_shape_it_polices() -> None:
    assert hand_spelled('println!("✓ clean");')
    assert hand_spelled('let s = "→ a\\n✗ b";')
    assert hand_spelled('format!("x\\n⚠ careful")')
    assert not hand_spelled('println!("{OK} clean");')
    assert not hand_spelled('let arrow = "a → b";')
    assert not hand_spelled('#[cfg(test)]\nmod tests {\n    const X: &str = "✓ y";\n}')
    assert hand_spelled(
        '#[cfg(test)]\nmod tests {\n    fn f() {}\n}\nfn g() { println!("✓ after"); }'
    )
    assert hand_spelled('#[cfg(test)]\nmod tests;\nfn g() { println!("✓ after"); }')
