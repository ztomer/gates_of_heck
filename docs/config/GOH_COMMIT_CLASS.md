# GOH_COMMIT_CLASS
Area: commit-messages
Kind: setting
Order: 10
Default: unset (rule off)

Set to `1` to run `goh commit-class` from the stock `commit-msg` hook (`gates/commit_msg.sh`) and
again over the pushed range in `push_gate.sh`, so `--no-verify` does not survive the push. A
`fix:`/`perf:` commit carries `Class:` (the invariant that broke, as a reusable phrase) and
`Siblings:` (the other sites, or `none (<the search>)`) **in git's trailer block** -- the last
paragraph, beside Co-Authored-By -- so `%(trailers)` reads them; a Class sharing half its words with
two or more earlier classes (the last 400 commits, read leniently) also carries `Systemic:` or
`Filed:` with substance (`none`, `n/a`, `tbd`, `-` are refused). Ported from a private Swift app's
prototype (roadmap Phase 8). A pushed range with no remote ref to bound it is left to the commit-msg
hook, said once. Known limit: `--amend` of a fix counts the commit being amended as an earlier
instance.
