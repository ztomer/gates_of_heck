# GOH_LANE_ADMIT
Area: claude-hooks
Kind: setting
Order: 10
Default: `refuse`

What a write spawn does with no room: `refuse` (the default since GOH.ADAPTLANES2, owner 2026-11-17
"Yes, make it enforce") refuses it as deferred, naming what the reading lacked; `warn` admits and
tells the session why (the ledger line says `warned`); `off` asks and records nothing; any other
word is `warn`, said as such. An `Owner-asked:` spawn and an unread reading admit in either mode.
