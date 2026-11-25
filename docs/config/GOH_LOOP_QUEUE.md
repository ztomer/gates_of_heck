# GOH_LOOP_QUEUE
Area: pushes
Kind: setting
Order: 316
Default: config.toml's `loop_queue`, else `.loop/queue.json` in the repos root

The loop's umbrella queue goh reads (`lib/queue_read.py`): the hooks, the filers and the audits read it
there, or in the `loop.db` beside it when one exists. A test names its own.
