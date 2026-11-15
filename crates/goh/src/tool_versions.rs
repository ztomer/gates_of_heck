//! The house tool versions, READ from `gates/required_tools.tsv` -- the one place a lint or
//! toolchain version is written (GOH.CLASS.PARITY, 2026-11-15).
//!
//! Before it the shellcheck floor was a `const` here beside the manifest's row (two places a raise
//! had to reach), the ruff pin was a literal in `gates/ruff_house.py`, the hosted workflow and four
//! install strings, and `pyformat` asked whatever ruff `PATH` gave. The table is compiled in
//! (`include_str!`), so the binary judges with the versions of the tree it was built from, the way
//! the shell gates read the same file at run time. `tests/test_tool_version_manifest.py` is red on
//! a version literal for a house tool written anywhere else.

const MANIFEST: &str = include_str!("../../../gates/required_tools.tsv");

/// The manifest's version cell for `tool`: its floor, or `=X.Y.Z` for a pin; `None` when no row
/// names one.
fn cell(tool: &str) -> Option<&'static str> {
    MANIFEST
        .lines()
        .filter(|l| !l.starts_with('#'))
        .map(|l| l.split('\t').collect::<Vec<_>>())
        .find(|c| c.len() == 6 && c[1] == tool && c[5] != "-")
        .map(|c| c[5])
}

/// The OLDEST release of `tool` a gate accepts (a pin's release counts as its floor too).
#[must_use]
pub fn floor(tool: &str) -> Option<&'static str> {
    cell(tool).map(|c| c.strip_prefix('=').unwrap_or(c))
}

/// The ONE release of `tool` a gate judges with, when the manifest pins it (`=X.Y.Z`).
#[must_use]
pub fn pin(tool: &str) -> Option<&'static str> {
    cell(tool).and_then(|c| c.strip_prefix('='))
}

#[cfg(test)]
mod tests {
    use super::{floor, pin};

    #[test]
    fn the_shellcheck_floor_and_the_ruff_pin_come_from_the_manifest() {
        let row = |tool: &str| {
            super::MANIFEST
                .lines()
                .find(|l| !l.starts_with('#') && l.split('\t').nth(1) == Some(tool))
                .and_then(|l| l.split('\t').nth(5))
        };
        assert_eq!(floor("shellcheck"), row("shellcheck"));
        assert_eq!(pin("shellcheck"), None);
        assert_eq!(pin("ruff").map(|p| format!("={p}")).as_deref(), row("ruff"));
        assert_eq!(floor("ruff"), pin("ruff"));
        assert_eq!(floor("no-such-tool"), None);
    }
}
