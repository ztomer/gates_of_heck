# GOH_LINE_BASELINE
Area: structural
Kind: setting
Order: 130
Default: unset (check skipped + warn when `GOH_LINE_EXCLUDE` is set)

Path to the repo's shrink-only ratchet baseline. Enables two checks: every `GOH_LINE_EXCLUDE` entry
over the cap carries a ceiling there — an exemption from the cap is not an exemption from every
bound — and the ratchet itself: each listed file is measured (`wc -l`) and may not exceed its
ceiling. Lower a number when a split lands; never raise one to absorb growth. Format:
`<lines><tab><path>` per line (`#` comments) or a JSON object mapping path to lines.
