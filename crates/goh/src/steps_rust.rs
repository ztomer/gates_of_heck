//! The cheap Rust source policies at COMMIT time: no `#[allow]`/`#[expect]`, no emptiness asserts,
//! and the release gate's `cargo fmt --check` (G1e, `crate::rustfmt_staged`), over the STAGED
//! `.rs` files.
//!
//! Both ran only in `rust_gate.sh`, at push. A commit with `assert!(x.is_empty())` passed the
//! pre-commit and was refused minutes later by the push (2026-10-06) -- a commit gate weaker than
//! the push gate. Each is a native scan of milliseconds. Staged files only, so an old violation in
//! a file the commit does not touch never blocks it; a commit with no compiled Rust source skips
//! the step (the push's whole-tree run owns the "matched nothing" refusal).
//!
//! "Has work" is decided AFTER `GOH_EXCLUDE`, by the same filter the scanners apply. It was
//! decided before it, and the empty-scope check after it: a commit whose only staged Rust was an
//! excluded vendored crate was refused as a blind scanner (`app_updates`, 2026-10-08).

use crate::step_report::{begin, fail, ok};
use crate::{emptyassert, gatesrc, noallow, steps};

#[must_use]
pub fn step(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    if !staged {
        return None;
    }
    let label = "rust source policies (staged)";
    let exclude = match steps::compile_exclude(&cfg.exclude) {
        Ok(x) => x,
        Err(message) => {
            let start = begin(label);
            return Some(fail(label, "native", &format!("{message}\n"), start));
        }
    };
    let rust: Vec<&String> = files
        .iter()
        .filter(|f| {
            f.as_bytes().ends_with(b".rs") && !exclude.as_ref().is_some_and(|x| x.is_match(f))
        })
        .collect();
    if rust.is_empty() {
        return None;
    }
    let start = begin(label);
    // Shape first, over EVERY staged `.rs` (tests, examples and build scripts included): the
    // release gate's `cargo fmt --all -- --check` judges them all (app_updates c0d3a57).
    match crate::rustfmt_staged::check(repo, &rust) {
        Err(message) => return Some(fail(label, "native", &format!("{message}\n"), start)),
        Ok(Some(report)) => return Some(fail(label, "native", &report, start)),
        Ok(None) => {}
    }
    if !rust.iter().any(|f| noallow::is_compiled_src(f)) {
        ok(label, start);
        return None;
    }
    match noallow::scan_root(repo, files, exclude.as_ref(), true) {
        Err(message) => return Some(fail(label, "native", &format!("{message}\n"), start)),
        Ok((hits, _)) if !hits.is_empty() => {
            return Some(fail(
                label,
                "native",
                &noallow::format_report(&hits, true),
                start,
            ));
        }
        Ok(_) => {}
    }
    let (code, out, err) = emptyassert::run(true, &cfg.exclude);
    if code != 0 {
        return Some(fail(label, "native", &format!("{out}{err}"), start));
    }
    ok(label, start);
    None
}
