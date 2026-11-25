# GOH_REQUIRES_CALL
Area: structural
Kind: setting
Order: 220
Default: unset (check skipped)

A repo-relative TOML file of rules for `goh requires-call`: a Python file that CALLS X must also
CALL Y, read from the AST (`ruff_python_parser`), so a docstring naming Y never satisfies it. Each
`[[rule]]` has `name`, `why` (printed with every violation), `files` (globs), `calls` (the trigger;
omit it and every matched file is bound), `must_call` (each a `module.function`, resolved through
the file's own imports, or a bare name: any call whose last name it is; either, or `*` for any
callee, may name string arguments, `cli("config-set")` or `*("theme", "--id")`, which must sit side
by side among the call's positional arguments, a list or tuple literal spread in place -- an
argument built at run time never matches) and an `[rule.exempt]` table of `"path" = "reason"`. An
exemption goes STALE, and fails, when its file is gone, no longer calls X, or now calls Y; a rule
that finds nothing to judge is exit 2 (the pattern moved). From a private Swift app's
`check_probe_placement.py` and `check_probe_courtesy.py` (2026-10-08).
