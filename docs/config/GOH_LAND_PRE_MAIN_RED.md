# GOH_LAND_PRE_MAIN_RED
Area: pushes
Kind: setting
Order: 140
Default: unset

The enqueue pre-step's extra nodes, one per line: the tests the idle full suite found failing on a
main tip main still holds (`lib/main_red.py`); `tools/affected_tests.py` adds the ones the tree
still defines to its set, so a lane sees main's red before its gate (GOH.MAINREDNOTICE). Internal:
the queue sets it.
