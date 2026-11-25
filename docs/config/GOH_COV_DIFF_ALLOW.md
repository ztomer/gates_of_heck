# GOH_COV_DIFF_ALLOW
Area: coverage
Kind: setting
Order: 120
Default: `diffcov_allow.json`

rust only: the diff coverage allowlist, relative to the repository's top. `{"entries": [{"file",
"text", "reason"}]}` exempts a changed line of `file` reading `text` (stripped) that cannot run on
this platform or is unreachable -- never a code-level suppression. A ratchet: an entry whose file is
gone, whose text is in no line, or whose every measured line is covered is stale and red; one with
no line record here (another platform's build) is kept; one without a reason is refused. Its content
keys the reused coverage verdict.
