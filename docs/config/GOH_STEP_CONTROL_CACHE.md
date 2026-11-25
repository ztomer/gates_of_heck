# GOH_STEP_CONTROL_CACHE
Area: runtime
Kind: setting
Order: 170
Default: `$XDG_CACHE_HOME/goh/step-control` (else `~/.cache/goh/step-control`)

Where `lib/step_control.py` keeps the rust control's pinned work: the fetched crate and its four
copies' build directories. Delete it to rebuild them; the first run after is cold.
