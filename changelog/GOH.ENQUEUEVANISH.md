* GOH.ENQUEUEVANISH: an enqueue that dies in its pre-step (a SIGKILL runs no cleanup) no longer vanishes. The reader that finds its marker dead (`lib/landq_inflight.py` `live`) files a red done record, `enqueue died in its pre-step: pid P ...`, so `goh land --wait`, the loop's watch and reconcile all see it; a supersede or `--cancel` (its tombstone) files nothing. The pre-step log is line-buffered, so a killed run keeps the lines it wrote; before, every live pre-step log read 0 bytes until it closed. Four lands vanished this way on 2026-11-23; who sends the kill is still open.

The enqueuer now names its own killer: on SIGTERM, SIGHUP, SIGINT, SIGQUIT, SIGUSR1/2, SIGALRM or
SIGXCPU it writes the signal and a full-width process table to its log, then dies of that signal as
before (lib/enqueue_witness.py). Three GOH.TIMINGDETECT enqueues died mid pre-step on 2026-11-23
with their pre-step group running on under init and nothing in the log to name a sender.
