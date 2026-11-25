# GOH_BARE_PYTHON_SCOPE
Area: structural
Kind: setting
Order: 290
Default: unset (hooks and `tools/gate.sh` only)

Regex on repo-relative paths ADDED to the scope of `no bare python in a hook or gate`
(`crates/goh/src/barepy/`). The default scope is what every repo has -- `.githooks/*` and
`tools/gate.sh` -- but a repo's local CI and build scripts are the same class: a person or a gate
runs them, and a bare `python3` there is whichever interpreter Homebrew linked last all the same.
Which of a repo's scripts those are is the repo's to say, so it says it here, e.g.
`'^(ci_local\.sh|build[^/]*\.sh|tools/[^/]*\.sh)$'` (ZeroThunder OW.42, 2026-10-31). Anchor it: the
match is `re.search`. Only shell sources are read (a `.sh` suffix or a shell shebang), and
`GOH_EXCLUDE` still wins over a path the scope names. A pattern that is not a regex refuses the step
NAMING THE KEY (exit 2), never a silent "no widening". Raise `GOH_BARE_PYTHON_MIN_FILES` with it, so
a scope that stops matching is red rather than clean.
