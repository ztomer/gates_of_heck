//! The source-line readers of `goh rust-scope` (`rust_scope.rs`, split for the line headroom,
//! GOH.GATEFLOOR): which reads a scope keys, and the literals a line names.

use std::path::{Path, PathBuf};

/// Which reads a scope keys: what the steps read, or only what the compiler reads.
///
/// `Run`: a test opening a file at run time too. `Compile`: `include!` and its kin, and a build
/// script, whose run is part of the build -- the scope of a group that compiles and never runs a
/// test (`rust_gate.sh`'s `crate` group: fmt, clippy, the manifest lints). goh's own crate opens
/// `CARGO_MANIFEST_DIR/../..` in its tests, so its run scope is the whole tree and every Python-only
/// land re-ran both clippy runs (GOH.GATEFLOOR); what a compile read outside this scope is still
/// caught after the run by `--check-depinfo`, and learned.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Reads {
    Run,
    Compile,
}

/// True when a line reads a file at COMPILE time (`include_str!`, `include_bytes!`, `include!`).
pub(crate) fn compile_time(line: &str) -> bool {
    ["include_str!", "include_bytes!", "include!"]
        .iter()
        .any(|m| line.contains(m))
}

/// True when a line's CODE reads a file at compile time: a macro above called on a literal, not a
/// string that holds one (`rust_scope_tests.rs` writes `include_str!(\"../../../VERSION\")` into a
/// fixture; read as a read, it climbed out of the repository and keyed goh's compile scope on the
/// whole tree).
/// A raw string holding such a call (`r#"include_str!("../x")"#`, a test of `rust_scope::reached`) splits
/// so that no literal holds the path: no read either.
pub(crate) fn compiles_a_read(line: &str) -> bool {
    ["include_str!(\"", "include_bytes!(\"", "include!(\""]
        .iter()
        .any(|m| line.contains(m))
        && string_literals(line).iter().any(|s| s.contains("../"))
}

/// The contents of the `"..."` literals on a line, `\` escapes honoured (a shell script written
/// from a test has `\"` inside its literal; splitting on `"` read that as no path at all).
pub(crate) fn string_literals(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur: Option<String> = None;
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match (&mut cur, c) {
            (None, '"') => cur = Some(String::new()),
            (Some(_), '"') => out.extend(cur.take()),
            (Some(s), '\\') => {
                if let Some(next) = chars.next() {
                    s.push(if next == 'n' { '\n' } else { next });
                }
            }
            (Some(s), other) => s.push(other),
            (None, _) => {}
        }
    }
    out
}

/// `a/b/../c` -> `a/c`, lexically; `None` if `..` climbs above the root of the path.
pub(crate) fn normalize(path: &Path) -> Option<PathBuf> {
    let mut out = PathBuf::new();
    for part in path.components() {
        match part {
            std::path::Component::ParentDir => {
                if !out.pop() {
                    return None;
                }
            }
            std::path::Component::CurDir => {}
            other => out.push(other),
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::{normalize, string_literals};
    use std::path::{Path, PathBuf};

    #[test]
    fn a_backslash_ending_the_line_ends_an_unclosed_literal_with_nothing() {
        assert_eq!(string_literals("\"a\\"), Vec::<String>::new());
        assert_eq!(string_literals("\"a\\nb\""), vec!["a\nb".to_owned()]);
    }

    #[test]
    fn a_current_dir_component_is_dropped() {
        assert_eq!(
            normalize(Path::new("./a/./b/../c")),
            Some(PathBuf::from("a/c"))
        );
        assert_eq!(normalize(Path::new("../x")), None);
    }
}
