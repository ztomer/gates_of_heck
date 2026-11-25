# GOH_LANDQ_WINDOW_MAX
Area: ship
Kind: setting
Order: 180
Default: `300`

The batch window's stretched bound, in seconds past the head's filing (GOH.ONEVERDICTTREE3,
`lib/landq_window.py`): an enqueue marker says when its pre-step started and the queue's done
records how long its repo's take (median `pre_s` of the newest 20, 480 s with none), so the wait
lasts to the latest marker DUE (start + that + 30 s) within this bound; a marker due later is not
waited for at all (GOH.WINDOWDUE: the hold would close on the bound with it still in its pre-step),
one with no start gets `GOH_LANDQ_WINDOW` alone. Never below `GOH_LANDQ_WINDOW`.
