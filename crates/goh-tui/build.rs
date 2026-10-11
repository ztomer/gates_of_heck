//! Reads `tui/stylerc` -- the one place the house's glyphs and colours are written -- and emits them
//! as constants, so a Rust binary prints what `tui/lib.sh` and `tui/lib.py` print and a glyph
//! changed there reaches it on the next build, with no code edit (BACKLOG 4.14).
//!
//! A missing stylerc, or one without a key, fails the build: a binary that quietly fell back to
//! glyphs of its own is the drift this crate exists to end.

use std::fmt::Write as _;
use std::path::Path;

/// Every key the crate exports, each with the constant it becomes.
const KEYS: [(&str, &str); 12] = [
    ("ICON_START", "START"),
    ("ICON_STEP", "STEP"),
    ("ICON_OK", "OK"),
    ("ICON_ERR", "ERR"),
    ("ICON_WARN", "WARN"),
    ("C_RESET", "C_RESET"),
    ("C_DIM", "C_DIM"),
    ("C_BOLD", "C_BOLD"),
    ("C_GREEN", "C_GREEN"),
    ("C_RED", "C_RED"),
    ("C_YELLOW", "C_YELLOW"),
    ("C_GRAY", "C_GRAY"),
];

fn main() -> Result<(), String> {
    let manifest = std::env::var("CARGO_MANIFEST_DIR").map_err(|e| e.to_string())?;
    let stylerc = Path::new(&manifest).join("../../tui/stylerc");
    println!("cargo:rerun-if-changed={}", stylerc.display());
    let text = std::fs::read_to_string(&stylerc)
        .map_err(|e| format!("goh-tui: cannot read {}: {e}", stylerc.display()))?;
    let mut out = String::new();
    for (key, name) in KEYS {
        let value = text
            .lines()
            .map(str::trim)
            .filter(|l| !l.starts_with('#'))
            .filter_map(|l| l.split_once('='))
            .find(|(k, _)| k.trim() == key)
            .map(|(_, v)| v.trim().trim_matches(|c| c == '"' || c == '\''))
            .ok_or_else(|| format!("goh-tui: {} has no {key}", stylerc.display()))?;
        // The stylerc spells ESC as the four characters backslash-0-3-3, as the shell and Python
        // halves decode it; the escape_default below writes it back as a Rust escape.
        let decoded = value.replace("\\033", "\u{1b}");
        let _ = writeln!(
            out,
            "pub const {name}: &str = \"{}\";",
            decoded.escape_default()
        );
    }
    let dest = Path::new(&std::env::var("OUT_DIR").map_err(|e| e.to_string())?).join("style.rs");
    std::fs::write(&dest, out).map_err(|e| format!("goh-tui: cannot write {}: {e}", dest.display()))
}
