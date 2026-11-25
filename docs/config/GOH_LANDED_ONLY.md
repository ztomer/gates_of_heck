# GOH_LANDED_ONLY
Area: commit-messages
Kind: setting
Order: 30
Default: unset (rule off)

A branch name (`main`). `push_gate.sh` refuses to push a NEW tip of that branch unless
`tools/land.sh` gated and merged that exact SHA: `land.sh` appends each SHA it lands to `goh-landed`
in the common git dir, and the push reads it before anything runs, naming the commit and the way to
land it. For a repo whose main moves only by `land.sh` from a branch worktree (this one), so a
commit made in the main checkout or a merge past the gate cannot be pushed (BACKLOG 1.5; it happened
twice on 2026-10-10). A tip the remote already has is not asked about. **The commit is refused too
(GOH.LANDONLY, 2026-11-08):** `gates/commit_gate.sh` (the stock pre-commit) refuses a commit in the
PRIMARY checkout while HEAD is that branch, before any gate runs (SRV.NTFYROTATE committed `5c4936f`
straight onto goh's main, and a later landed tip carried it to origin). A linked worktree, another
branch, a detached HEAD and a repo that names no branch are untouched; the branch is read from
`.gatesrc`, never from the caller's environment. The ways through are `GOH_LAND_CALLER=land.sh` (set
by `tools/land.sh` on its merge, nowhere else) and the documented human escape
`GOH_MAIN_COMMIT_OK='<reason>' git commit ...` (an empty reason is no escape);
`tools/release-kit/release.sh` makes no commit of its own unless it must cut a stanza from
Unreleased, which for a landed-only repo is refused here too: cut it in the version commit, in a
worktree, and land it.
