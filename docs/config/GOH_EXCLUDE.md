# GOH_EXCLUDE
Area: structural
Kind: setting
Order: 110
Default: unset

Regex on repo-relative paths that are NOT OURS TO RE-STYLE: vendored third-party source, generated
code. **Exempt from**: the emoji scan, the length cap (with `GOH_LINE_EXCLUDE`), shell lint,
early-exit pipe, code after exec, bare hook index, release quiet, BSD-only mktemp, locale variable
set by bash, bare open, run paths, hedged plural, hand-rolled TUI mark, git deadline, dead port,
bare python, layer parity (an excluded file is no language the repo holds), home paths,
kill-by-name, unreaped spawn, subprocess-stdin, md-links, claim-derivation, roadmap (the plan files
besides `GOH_ROADMAP` it looks for), `#[allow]`/`#[expect]` and emptiness asserts (commit and push),
and `--each-crate`'s per-crate fmt/clippy/test/coverage (an excluded crate is gated through whatever
first-party crate builds it; the pattern is matched against the crate's MANIFEST path,
`crates/vendored-rs/Cargo.toml`, so `'^crates/vendored-rs/'` excludes it -- until 2026-10-08 it was
matched against the bare directory, where a trailing-slash pattern never matched, and by `grep -E`,
which refused a look-ahead; `rust_gate.sh --each-crate --list` prints what is left), and
`py_staged.sh`'s ruff, staged and `--all` alike (a vendored `.py` is not ours to re-format; until
2026-11-18 only `--all` dropped it, so a commit refused the vendored file its push exempted --
GOH.PYSTAGEDEXCLUDE), and `py_lint_ceiling.py --seed` (an excluded file's findings are no debt the
repo carries). **Every entry point reads it the same way**: the structural gate, a direct `goh
<check>` or `goh.sh <check>` given no `--exclude` (it reads the repo's `.gatesrc`; `--exclude ''`
opts out), and the R3 estate sweep over a consumer's corpus. Until 2026-10-08 a direct check ignored
the file, so `goh.sh md-links` in `app_updates` reported a vendored dead anchor its own gate
exempts. The AMBIENT environment never counts: an inherited `GOH_EXCLUDE` is not the repo's
declaration. **Never exempt from the secrets scan**, by construction: `goh secrets` takes no
exclusion at all -- nor the checkout-credentials scan (`goh checkout-credentials`), whose one escape
is `# persist-credentials-ok: <reason>` on the step, nor the tap-write scan (`goh tap-writes`),
whose one escape is `# tap-write-ok: <reason>` on the line, nor the moving-tag scan (`goh
moving-tags`), whose one escape is `# moving-tag-ok: <reason>` on the reference -- what a build
pulls is not a style, vendored or not -- nor the workflow-pin scan (`goh workflow-pins`), whose one
escape is `# workflow-pin-ok: <reason>` on the reference -- what CI runs on is not a style. A path
pattern written for style cannot say anything about a leak, and until 2026-10-08 this one silently
reached the credential scan too -- `app_updates` excluded a vendored crate and 57 of its 63 staged
files went unscanned (ztools had grown a separate vendor secrets step to cover the same hole). There
is deliberately no second key for "vendored": with two, `GOH_EXCLUDE` could still drop the scan;
with none, nothing can. A real vector in vendored code is fixed or carries `secret-ok: <reason>` on
its line, as first-party code does. An exclusion that leaves a commit with no Rust of its own to
scan is nothing to judge, not a blind scanner: the "matched NO compiled source" refusal fires only
at full scope and only on a `Cargo.toml` the exclusion leaves in scope. **Anchor it.** The match is
`re.search`, so `'vendor/'` is a SUBSTRING test: it exempts `rust/src/myvendor/a_tests.rs` exactly
as it exempts `vendor/camoufox-rs`, silently. Write `'^vendor/'`. Measured 2026-10-04 on `ztools`,
which declares the unanchored form: with it, 106 first-party test files stay in scope and a
violation planted in one is still red — so it is narrow in effect today — while the same violation
at a path merely *containing* `vendor/` is exempt with no output at all.
