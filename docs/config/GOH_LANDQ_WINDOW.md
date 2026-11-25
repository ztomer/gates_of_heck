# GOH_LANDQ_WINDOW
Area: ship
Kind: setting
Order: 170
Default: `120`

The batch window, in seconds past the queue head's filing (GOH.BATCHWINDOW, `lib/landq_window.py`):
while `enqueuing/` holds a live marker of another land of the head's repo (its pre-step still
running), the lander waits for it to file, so both go through ONE land gate; it stops early when no
such marker is left or the batch is full, and never waits for its own marker or a dead pid's. Each
wait is logged in `lander.log`. `0` turns the wait off.
