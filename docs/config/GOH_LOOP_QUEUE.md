# GOH_LOOP_QUEUE
Area: ship
Kind: setting
Default: `.loop/queue.json` under the projects root

The loop's umbrella queue, by path (`lib/queue_read.py`): the hooks, the filers and the audits read
it there, or in the `loop.db` beside it when one exists. A test names its own.
