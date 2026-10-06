//! The cheap Rust source policies at COMMIT time: no `#[allow]`/`#[expect]`, no emptiness asserts,
//! over the STAGED `.rs` files.
//!
//! Both ran only in `rust_gate.sh`, at push. A commit with `assert!(x.is_empty())` passed the
//! pre-commit and was refused minutes later by the push (2026-10-06) -- a commit gate weaker than
//! the push gate. Each is a native scan of milliseconds. Staged files only, so an old violation in
//! a file the commit does not touch never blocks it; a commit with no compiled Rust source skips
//! the step (the push's whole-tree run owns the "matched nothing" refusal).

use crate::step_report::{begin, fail, ok};
use crate::{emptyassert, gatesrc, noallow, steps};

#[must_use]
pub fn step(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    if !staged
        || !files
            .iter()
            .any(|f| f.as_bytes().ends_with(b".rs") && noallow::is_compiled_src(f))
    {
        return None;
    }
    let label = "rust source policies (staged)";
    let start = begin(label);
    let exclude = match steps::compile_exclude(&cfg.exclude) {
        Ok(x) => x,
        Err(message) => return Some(fail(label, "native", &format!("{message}\n"), start)),
    };
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
