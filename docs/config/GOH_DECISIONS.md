# GOH_DECISIONS
Area: structural
Kind: setting
Order: 320
Default: unset (check skipped)

Set to `on` to run `goh decisions` in the structural pipeline over the decision ledger,
`docs/decisions/D-NNNN-<slug>.md` (the decision-ledger skill's schema): the ledger is not empty;
each record opens `# D-NNNN — title` with the filename's id; each of the seven fields (`Question`,
`Evidence`, `Options priced`, `Ruling`, `Falsifier`, `Re-open when`, `Lives in`) is present and
non-empty, a field wrapping onto indented lines; every backticked `Lives in` token that reads as a
path (a `/` or a file extension) is a tracked file or directory, and any other is a symbol `git grep
-w` finds outside the ledger; ids are unique, no Question is asked twice, and every `D-NNNN` a
record cites exists. At `--staged` the records and the tree are the index's, so a commit that
deletes a path a record names is red. Any other value is exit 2 NAMING THIS KEY.
