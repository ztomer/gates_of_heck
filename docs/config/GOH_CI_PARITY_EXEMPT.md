# GOH_CI_PARITY_EXEMPT
Area: local-ci
Kind: setting
Order: 70
Default: unset (no exemptions)

Opt-in input of `checks/check_ci_local_parity.py` (a `GOH_CI_STEPS` step: `python3
$GOH_DIR/checks/check_ci_local_parity.py`). One `path: reason` per line: a script CI runs that
genuinely cannot run before a push (packaging, a deploy). A reason is required (exit 2 without). A
RATCHET, red both ways: an entry CI no longer runs, or that the local gate now runs, is stale.
