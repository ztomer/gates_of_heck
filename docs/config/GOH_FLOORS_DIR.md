# GOH_FLOORS_DIR
Area: runtime
Kind: internal
Default: the action's `goh-dir` input

Where the goh-floors action (`.github/actions/goh-floors/action.yml`) finds the goh checkout; empty,
the checkout the action is in. The action sets it from its `goh-dir` input, which is what a workflow
writes. Not a setting.
