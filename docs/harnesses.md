# The screen-presentation harness: two halves, one invariant

Nothing may render to the user's display from tests or unattended runs.
One invariant, enforced from two directions that do not subsume each other:

## Static half — `checks/check_no_screen_presentation.py`

Reads TEST TARGET SOURCES (paths or `--scope` glob) for per-language
presentation APIs (.swift / .m/.mm / .py pattern sets; see the module
docstring). It sees what a test ASKS for. Exit 1 names every
`file:line: API`. Escape hatch: a `screen-ok: <reason>` marker on the
offending line or above.

Blind spot, stated: it cannot see a change that makes an offscreen path
create a presentation surface anyway. That direction is the runtime half's.

## Runtime half — `lib/headless_env.sh`

Source it; it publishes the `GOH_HEADLESS=1` contract:

- `headless_env` — emits `GOH_HEADLESS=1` assignments for child launches:
  `env $(headless_env) ./bin/App --render-one`.
- `headless_enforced` — true when the policy is on (truthy values only;
  `"" 0 false no off` mean off).
- `headless_require_live <name>` — the LIVE-tier declaration. Refuses by
  EXITING 3 while the policy is enforced — distinct from 1 so a runner can
  tell "refused, wrong tier" from "ran and failed". Attended runs opt out
  with `env -u GOH_HEADLESS`.

GUI-facing programs should refuse under `GOH_HEADLESS=1` themselves; this
library is how harnesses forward the policy and declare their tier.

## Wiring

Both halves are opt-in until a third repo adopts them (layer-3 to layer-1
graduation). A repo opts in by adding to its gate script:

    goh_step "no screen presentation" \
        python3 "$CHECKS/check_no_screen_presentation.py" --scope 'tests/*'

and sourcing `$GOH_DIR/lib/headless_env.sh` in any live-tier harness.
