* `goh undo FILE...` (GOH.UNDOMINE, `tools/undo.py`): the safe undo of one's OWN edits that agents
  kept reaching for `git checkout --`, `git stash` and `git reset` to do (65 refusals in 7 days).
  It restores a file the caller changed in its own linked worktree -- to HEAD, or to the lane's
  base with `--base` -- and keeps the replaced text under `<worktree>/.goh-undo/` for one step
  back (`goh undo --back FILE`). It refuses, changing nothing, a path outside the caller's
  worktree, a repo's main checkout, a held repo and a worktree goh's lane records give to another
  item (`--item`, `GOH_ITEM`). The discard guard's "would throw away uncommitted work" refusal and
  the stash refusal now print the matching `goh undo` command.
