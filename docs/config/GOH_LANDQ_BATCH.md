# GOH_LANDQ_BATCH
Area: ship
Kind: setting
Order: 160
Default: (derived)

The most land requests of one repo the lander judges with ONE land gate (GOH.LANDBATCH,
`lib/landq_batch.py`). Unset, it is derived per pick from the repo's red rate at its gate
(GOH.ADAPTBATCH, `lib/landq_size.py`: the k minimising expected gates per landed item, 1 to 6; 3
with fewer than 10 gated attempts on record), logged with why in `lander.log`; set, it wins: the
queue's head and the land requests of its repo behind it, in queue order, stacked (each rebased onto
the one below) under the land lock and gated on the tip; green lands each item at its own commit,
red bisects so one bad item never fails the others, a conflict drops only that item. `1` is the
one-by-one queue.
