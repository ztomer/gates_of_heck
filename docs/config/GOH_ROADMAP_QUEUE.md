# GOH_ROADMAP_QUEUE
Area: structural
Kind: setting
Order: 340
Default: `$HOME/Projects/.loop/queue.json`

The umbrella queue `goh roadmap` asks which cited items are done (`--queue PATH` wins). When
`loop.db` exists beside that path, the statuses come from it through `sqlite3 -readonly`
(LOOP.QUEUEARCHIVE); a machine without `sqlite3` gets rule 4 NOT JUDGED like an absent file. A SEAM,
read from the environment and never from `.gatesrc`: a test names a fixture, and a machine without
the umbrella (or a queue that will not parse) gets rule 4 NOT JUDGED with the reason printed --
never a crash, never a silent pass -- and the seed's `queue-done` entries withheld, so a rule that
could not run is not read as fixed.
