# GOH_HEADLESS
Area: runtime
Kind: setting
Order: 40
Default: unset (policy off)

`1` (or truthy) enforces offscreen policy; `"" 0 false no off` mean off. Forward with `env
$(headless_env)`. Unset with `env -u GOH_HEADLESS` for deliberate live runs. See
`docs/harnesses.md`.
