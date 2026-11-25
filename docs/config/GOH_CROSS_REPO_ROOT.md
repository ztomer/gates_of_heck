# GOH_CROSS_REPO_ROOT
Area: structural
Kind: setting
Order: 170
Default: unset (not needed)

For `gates/push_gate.sh`, and set BY it: the real checkout of the repo being pushed. The export
worktree holds the pushed commit's TRACKED files, so a repo whose evidence is its sibling
repositories — a metarepo, whose children are separate repos rather than submodules — has none of
that evidence in the export, and every cross-repo citation resolves to nothing. Measured 2026-10-03
on `games`: 9 findings on a commit whose checker exits 0 in the working tree. A checker may read
this ONLY for citations that name a child; it is deliberately not a symlink of the children into the
worktree, which would put sibling sources inside the parent's scanned tree and fail a parent push on
a child's finding. The worktree's isolation is untouched: it guards a repo's OWN files, and those
still come from the worktree.
