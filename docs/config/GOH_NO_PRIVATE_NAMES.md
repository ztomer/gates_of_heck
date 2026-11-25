# GOH_NO_PRIVATE_NAMES
Area: structural
Kind: setting
Order: 240
Default: unset (check skipped)

Set to `on` in a PUBLIC repo to run `goh private-names`: no tracked line or path names a private
repository. The names are the owner's, read from the private seed store's `private_names.txt` (the
estate seeds' own directory, `~/.local/state/goh/seeds` by default; one name per line, `#` comments,
matched case-sensitively at word edges; a side where the entry ends in a separator -- `word/`,
`/word`, a backticked `` `word` `` -- needs no edge, so a repository named by an English word is
listed by its path forms and the prose word stays free), never from a tree; a machine with no list
(a GitHub runner) says `not judged` on a warning line.
