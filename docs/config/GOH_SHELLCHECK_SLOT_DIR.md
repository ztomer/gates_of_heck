# GOH_SHELLCHECK_SLOT_DIR
Area: pushes
Kind: setting
Order: 490
Default: `~/.cache/goh/slots/shellcheck`

The shellcheck token files, never removed. The suite does NOT privatise it as it does
`GOH_GATE_SLOT_DIR`: the memory is the machine's, and most of the incident's shellchecks were
tests'. Set it only to isolate a test that fakes shellcheck.
