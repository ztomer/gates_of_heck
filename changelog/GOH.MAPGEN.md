* GOH.MAPGEN: the script map is rendered, not committed (owner, 2026-11-24: "Render on demand").
  `goh map` prints the inventory, `goh map NAME` one file's whole header with its callers and tests,
  `goh map --rev TAG` the same of a release (one before this answers from its committed map). Each
  file's map prose moved into its own header (a docstring, a top comment block, `//!`); a script
  that says nothing of itself is refused, and so is a header that speaks for a path the tree does
  not hold. docs/map.md is a pointer; three lanes went red on a conflict in it in one day.
