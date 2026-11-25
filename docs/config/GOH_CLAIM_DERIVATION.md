# GOH_CLAIM_DERIVATION
Area: structural
Kind: setting
Order: 210
Default: unset (check skipped)

Set to `1` to run `goh claim-derivation`: a **MARKED** number in prose is re-derived from the tree.
Three adversarial reviews of the games estate converged on one class (2026-10) — `roadmap_state.py`
claimed "64 declared gates" against 70 in `verify.py`, `check_mcp_server.py` claimed "477 lines and
18 characterization tests" for 321 and 20, `gaf/README.md` claimed 86 files / 1059 tests for 101 /
1732, and each was found by hand at hours apiece. **The claim form is explicit, and the looser rule
was rejected**: a claim is read only when the number AND the thing it counts are both in backticks
(this estate's existing convention that a backtick means "this is the evidence"), or when the line
carries a leading `claim:` marker. A bare number beside a path is prose, because reading bare
numbers means reading English — a gate cannot tell an inventory from an incident, a current claim
from a quoted one, or a count from a version, and it is wrong LOUDLY when it is wrong. **Four units,
each derived exactly**: `lines` (`awk 'END{print NR}'` — not `wc -l`, which reads a file with no
final newline one line short), `tests` (test functions by `ast`, a parametrised case counting once),
`gates`/`steps`/`checks` (elements of a NAMED module-level list, by `ast` — never a line regex,
which is what read 64 against 70), and `files` (the length of `git ls-files`'s own answer for
`DIR/*.EXT`). Every finding prints the command that re-derives the truth, and `--probe` EXECUTES
those commands against fixtures, so a finding a reader cannot check cannot ship. Survivors go in
`claim_derivation_allow.json` at the repo root: one claim in one file each, with a `reason` and a
`status` (`legitimate` or `unreviewed`); **a stale entry fails**, and the key is the claim itself,
whitespace collapsed, not its line, so neither `ruff format` nor a re-wrapped paragraph can revoke
an exemption while a changed number does. Opt-in per repo, seeded first: this lands RED in every
repo in the estate, because every repo in the estate has this defect, and a gate that goes red in
twenty places on the day it lands is a gate that gets disabled. Fenced blocks are examples in every
text file, and in Python a non-docstring string literal is data rather than prose — that is where a
checker's own fixture lives, and a gate that cannot express its test cases cannot be tested. Same
`GOH_EXCLUDE`.
